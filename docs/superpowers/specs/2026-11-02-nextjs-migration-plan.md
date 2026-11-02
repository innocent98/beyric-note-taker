# Next.js migration plan

**Status:** DECIDED 2026-11-02 (Adebayo): **move the frontend to Next.js**. This supersedes the "stay on Jinja" decision in `2026-11-02-frontend-approach.md` (kept for the record). Reasons he gave: a sleek UI, parity with the designer's handoff, Next.js's rendering and optimisation. Lead's supporting reason: the designer's `.dc.html` files are stateful component prototypes (dropdowns, dialogs, boards, per-screen state), which port almost one-to-one to React components and would have to be rebuilt as server round trips in Jinja.

Goal: pixel and behaviour parity with `docs/ui/Beyric Note Taker redesign/`, a sleek app, server-rendered public pages that rank (SEO, AEO, GEO), PWA, responsive, one brand. The pilot stays up throughout.

## Architecture: strangler pattern, same origin, no nginx change

```
internet -> nginx (unchanged) -> 127.0.0.1:8020 -> Next.js (front door)
                                                     |- pages it owns: rendered by Next
                                                     `- everything else: `rewrites.fallback` proxies to FastAPI (Jinja, /api, /health, webhooks)
```

- Next.js (App Router, TypeScript, standalone output) lives in `web/`, runs as a compose service `next`, and takes over host port `127.0.0.1:8020`. FastAPI (`web` service) stops publishing 8020 and is reached only on the internal network.
- Next's `rewrites.fallback` sends any path Next has no page for to FastAPI. So a page is migrated by adding it in Next; unmigrated Jinja pages keep working untouched. Same origin, so the signed session cookie works with no change.
- `deploy/deploy.sh`'s health check (`127.0.0.1:8020/health`) keeps working because `/health` falls through to FastAPI.
- FastAPI stays the system of record: auth, database, worker, Vexa, OpenAI, and (later) Google OAuth and the Pub/Sub endpoint. It gains a JSON API under `/api/` for everything the Jinja pages render (stream B).
- Auth: the signed FastAPI session cookie. Next server components call the JSON API with the incoming cookie forwarded; mutating calls send the CSRF token from `GET /api/session`.
- CSP: pages Next renders get a nonce-based CSP from middleware (no `unsafe-inline`); proxied FastAPI responses keep FastAPI's own CSP header untouched. To be proven in the spike.
- Fonts, tokens, components: `app/static/tokens.css`, the fonts and `components.css` are ported to `web/` (they are plain CSS). Jinja macros become React components. The no-colour-literal, no-inline-style and "every component in the style guide" guards are ported as tests.

## Streams

| Stream | Scope | Branch | Touches |
|---|---|---|---|
| **A: Next scaffold and spike** | `web/` app, Dockerfile, compose service, port tokens/fonts/components, CSP nonce, fallback proxy, cookie forwarding, memory measurement, the public pages (landing, privacy, terms) rebuilt with SEO basics, style guide page | `feature/nextjs-scaffold` | `web/`, `compose.yaml`, `deploy/`, `.github/workflows` (path filters) |
| **B: JSON API** | `/api/` endpoints for session, meetings, templates, vocabulary; CSRF header; tests; captured real responses; FE integration guide | `feature/json-api` | `app/` (Python), `tests/`, `docs/fe-integration-guide-*.md` |
| **C: owner app screens** (after A and B are merged) | login, meetings list and detail, templates, vocabulary, to designer parity | `feature/nextjs-app-screens` | `web/` |
| **D: new designed screens** (after C) | accounts and pilot, calendar and auto-join, team, tasks, sprints, share and download, translation | one branch each | `web/`, `app/` |

A and B do not touch the same files, so they run in parallel. C needs both; D builds on C. The auto-join design (brainstorm, spec) can run in parallel with C and D as a design-only stream.

## Risks and how the spike answers them

| Risk | Spike question |
|---|---|
| Next server memory on the shared 7.8 GB box (Vexa is capped at 3 GB) | Memory of the standalone production container idle and under a few requests. Put a `mem_limit` on it |
| Strict CSP vs Next's inline hydration scripts | Does nonce middleware give a working page with no `unsafe-inline`? |
| Fallback proxy correctness | Do cookies, `Set-Cookie`, redirects (303), POST bodies and headers pass through unchanged for the Jinja pages? |
| Deploy and rollback | Can `deploy/deploy.sh` build and health-check both services and roll back? |
| CI cost | Path filters so docs-only and Python-only changes skip the Node jobs, and the reverse |

## Rules for every stream

Global rules in `~/.claude/CLAUDE.md` apply: no AI attribution anywhere in git or GitHub; no `claude/` branches; an SOP, a checklist update and (for B) a verified FE integration guide with every shipment; fast local checks only (CI runs the full suite); open a PR and never merge (Adebayo says "merge #N"); never deploy while a meeting is `bot_sent` or `processing`.
