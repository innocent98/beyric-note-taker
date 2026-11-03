#!/usr/bin/env node
// A stand-in for FastAPI's JSON API, for looking at the owner screens without Docker, a database
// or a worker. It replays the captures in docs/fe-captures/owner-app/ as meetings 1 to 10, so every
// meeting state has a page. Nothing here is the real backend: writes answer with the capture shape
// and change nothing, and any password signs in.
//
//   node scripts/mock-api.mjs --port 8031
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run dev        (or build, then start)
//
// Visit /login and sign in with any password. Start with --empty for the empty list, --stale for
// the worker banner.
import http from "node:http";
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8031" },
    empty: { type: "boolean", default: false },
    stale: { type: "boolean", default: false },
  },
});

const captures = join(dirname(fileURLToPath(import.meta.url)), "../../docs/fe-captures/owner-app");
const body = (name) => JSON.parse(readFileSync(join(captures, `${name}.json`), "utf8")).response.body;

// One meeting per state, from the captures. Titles vary so the list reads like a real one.
const details = [
  ["47-meeting-detail-ready", "Beyric Monday planning"],
  ["43-meeting-detail-processing", "Client call with Acme"],
  ["35-meeting-detail-bot-sent", "Google Meet kqz-mnwp-tuv"],
  ["49-meeting-detail-failed-retryable", "Design review"],
  ["50-meeting-detail-failed-no-speech", "Team stand-up"],
  ["48-meeting-detail-ready-removed", "Interview, backend role"],
  ["41-meeting-detail-zoom-second-attempt", "Zoom 842 1190 4471"],
].map(([file, title], index) => {
  const meeting = body(file);
  const age = new Date(Date.UTC(2026, 10, 2, 9 + index, 30)).toISOString();
  return { ...meeting, id: index + 1, title, created_at: age };
});
const summary = ({ id, title, platform, native_id, status, step, status_text, status_tone, template_id, created_at }) => ({
  id, title, platform, native_id, status, step, status_text, status_tone, template_id, created_at,
});

const templates = body("11-templates-seeded").templates;
const terms = body("19-vocabulary-list").terms;

function json(res, status, payload, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store", ...headers });
  res.end(JSON.stringify(payload));
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname.replace(/\/$/, "");
  const loggedIn = /(^|;\s*)session=owner/.test(req.headers.cookie ?? "");
  const method = req.method ?? "GET";
  req.resume();

  if (path === "/api/session") {
    if (method === "POST") {
      return json(res, 200, { authenticated: true, csrf_token: "mock-csrf" }, { "set-cookie": "session=owner; Path=/; HttpOnly; SameSite=Lax" });
    }
    if (method === "DELETE") {
      return json(res, 200, { authenticated: false, csrf_token: "mock-csrf" }, { "set-cookie": "session=; Path=/; Max-Age=0" });
    }
    return json(res, 200, loggedIn ? { authenticated: true, csrf_token: "mock-csrf" } : { authenticated: false, csrf_token: "mock-csrf" });
  }
  if (path.startsWith("/api/") && !loggedIn) {
    return json(res, 401, { error: { code: "not_authenticated", message: "Log in to continue." } });
  }
  if (path === "/api/meetings" && method === "GET") {
    const meetings = values.empty ? [] : details.map(summary).reverse();
    return json(res, 200, { meetings, has_more: false, next_before_id: null, worker_stale: values.stale || values.empty });
  }
  const detail = /^\/api\/meetings\/(\d+)$/.exec(path);
  if (detail && method === "GET") {
    const meeting = details.find((m) => m.id === Number(detail[1]));
    return meeting ? json(res, 200, meeting) : json(res, 404, { error: { code: "not_found", message: "That meeting was not found." } });
  }
  if (path === "/api/templates" && method === "GET") return json(res, 200, { templates });
  if (path === "/api/vocabulary" && method === "GET") return json(res, 200, { terms });
  if (path.startsWith("/api/")) return json(res, 200, {});
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found (mock api)");
});

server.listen(Number(values.port), "127.0.0.1", () => {
  console.log(`mock api on http://127.0.0.1:${values.port}`);
});
