#!/usr/bin/env node
// A stand-in for FastAPI's JSON API, for looking at the owner screens without Docker, a database
// or a worker. It replays the captures in docs/fe-captures/owner-app/ as meetings 1 to 10, so every
// meeting state has a page. Nothing here is the real backend: writes answer with the capture shape
// and change nothing, and any email and password sign in.
//
//   node scripts/mock-api.mjs --port 8031
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run dev        (or build, then start)
//
// Visit /login and sign in with any email and password. Start with --empty for the empty list, --stale for
// the worker banner.
//
// The accounts routes (sign-up, confirm email, reset password, waiting list, Google) replay
// docs/fe-captures/pilot-accounts/. Start with --pilot closed|open|full (default closed, which is what
// the real pilot is), --no-google to hide the Google button, --prefill you@example.test for a visitor
// the full pilot turned away, and --google-outcome ok|pilot_full|<code> (google_denied, google_failed, terms_required, ...) for where
// /api/auth/google/start sends the browser. Magic values, so every state can be reached by hand:
//   sign-up: password "password" -> 422 password_too_weak; email "rate@x.test" -> 429; invite "bad" -> 400
//   confirm: token "dead" -> 400 expired, token "full" -> 409 pilot_full, password "wrong" -> 400 mismatch
//   reset:   token "dead" -> 400 expired, password "password" -> 422 password_too_weak
//   sign-in: password "wrong" -> 401, email "locked@x.test" -> 429, email "gateway@x.test" -> a 429 page that is not JSON
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
    pilot: { type: "string", default: "closed" },
    "no-google": { type: "boolean", default: false },
    prefill: { type: "string" },
    "google-outcome": { type: "string", default: "google_denied" },
  },
});

const captureDir = join(dirname(fileURLToPath(import.meta.url)), "../../docs/fe-captures");
const captures = join(captureDir, "owner-app");
const body = (name) => JSON.parse(readFileSync(join(captures, `${name}.json`), "utf8")).response.body;
const prelaunchBody = (name) => JSON.parse(readFileSync(join(captureDir, "prelaunch-mail-prefs", `${name}.json`), "utf8")).response.body;
const accountBody = (name) => JSON.parse(readFileSync(join(captureDir, "pilot-accounts", `${name}.json`), "utf8")).response.body;

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

let notifyEmail = true;
const MOCK_USER = { email: "owner@example.test", name: null, is_admin: false, email_verified: true };

const readJson = (req) =>
  new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "null"));
      } catch {
        resolve(null);
      }
    });
  });

// The accounts routes. Each answer is the body of a capture from the running app (the file is named
// in the comment), so a screen built here sees the shapes the real API sends.
function accounts(path, method, data, res) {
  const email = String(data?.email ?? "");
  if (path === "/api/pilot" && method === "GET") {
    return json(res, 200, {
      state: values.pilot, // 04 closed, 11 open, 57 full
      prefill_email: values.prefill ?? null, // 122
      google_signin: !values["no-google"], // 02 is the same shape with false
    });
  }
  if (path === "/api/signup" && method === "POST") {
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    if (data?.invite_token === "bad") return json(res, 400, accountBody("82-signup-with-invite-wrong-address"));
    if (!data?.invite_token && values.pilot === "closed") return json(res, 403, accountBody("05-signup-while-closed"));
    if (!data?.invite_token && values.pilot === "full") return json(res, 409, accountBody("59-signup-pilot-full"));
    if (data?.accept_terms !== true) return json(res, 422, accountBody("13-signup-terms-not-accepted"));
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("14-signup-bad-email"));
    if (data?.password === "password") return json(res, 422, accountBody("12-signup-weak-password"));
    return json(res, 202, accountBody("18-signup-ok"));
  }
  if (path === "/api/signup/resend" && method === "POST") {
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    return json(res, 202, accountBody("35-resend-unverified"));
  }
  if (path === "/api/verify-email" && method === "POST") {
    if (data?.token === "dead") return json(res, 400, accountBody("23-verify-unknown-token"));
    if (data?.token === "full") return json(res, 409, accountBody("58-verify-pilot-filled-up"));
    if (data?.password === "wrong") return json(res, 400, accountBody("22-verify-wrong-password"));
    return json(res, 200, accountBody("24-verify-ok"));
  }
  if (path === "/api/password/forgot" && method === "POST") {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("14-signup-bad-email"));
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    return json(res, 202, accountBody("39-forgot-known"));
  }
  if (path === "/api/password/reset" && method === "POST") {
    if (data?.token === "dead") return json(res, 400, accountBody("43-reset-bad-token"));
    if (data?.password === "password") return json(res, 422, accountBody("42-reset-weak-password"));
    return json(res, 200, accountBody("44-reset-ok"));
  }
  if (path === "/api/waitlist" && method === "POST") {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("51-waitlist-bad-email"));
    return json(res, 202, accountBody("49-waitlist-join"));
  }
  return null;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname.replace(/\/$/, "");
  const loggedIn = /(^|;\s*)session=owner/.test(req.headers.cookie ?? "");
  const method = req.method ?? "GET";
  const data = ["POST", "PUT", "PATCH"].includes(method) ? await readJson(req) : (req.resume(), null);

  // The real API refuses a write without the token from GET /api/session, public routes included.
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && path.startsWith("/api/") && req.headers["x-csrf-token"] !== "mock-csrf") {
    return json(res, 403, accountBody("17-signup-without-csrf"));
  }

  if (path === "/api/auth/google/start" && method === "GET") {
    if (values["no-google"]) return json(res, 404, accountBody("03-google-start-unavailable"));
    const outcome = values["google-outcome"];
    if (outcome === "ok") {
      res.writeHead(302, { location: "/", "set-cookie": "session=owner; Path=/; HttpOnly; SameSite=Lax" });
    } else if (outcome === "pilot_full") {
      res.writeHead(302, { location: "/pilot-full" });
    } else {
      res.writeHead(302, { location: `/login?error=${outcome}` });
    }
    return res.end();
  }
  const account = accounts(path, method, data, res);
  if (account !== null) return account;

  if (path === "/api/session") {
    if (method === "POST") {
      if (String(data?.email ?? "").startsWith("locked@")) return json(res, 429, body("80-limit-sixth-attempt"));
      if (String(data?.email ?? "").startsWith("gateway@")) {
        res.writeHead(429, { "content-type": "text/html" });
        return res.end("<html><body><h1>429 Too Many Requests</h1><hr>nginx</body></html>");
      }
      if (data?.password === "wrong") return json(res, 401, accountBody("21-login-before-verifying"));
      return json(res, 200, { authenticated: true, csrf_token: "mock-csrf", user: MOCK_USER, usage: { used_seconds: 0, cap_seconds: 18000 } }, { "set-cookie": "session=owner; Path=/; HttpOnly; SameSite=Lax" });
    }
    if (method === "DELETE") {
      return json(res, 200, { authenticated: false, csrf_token: "mock-csrf", user: null, usage: null }, { "set-cookie": "session=; Path=/; Max-Age=0" });
    }
    return json(res, 200, loggedIn ? { authenticated: true, csrf_token: "mock-csrf", user: MOCK_USER, usage: { used_seconds: 0, cap_seconds: 18000 }, unread_notifications: 2 } : { authenticated: false, csrf_token: "mock-csrf", user: null, usage: null });
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
  // Email preferences and the bell: the captures in docs/fe-captures/prelaunch-mail-prefs/. The
  // switch keeps its state in memory so the screen can be flipped by hand.
  if (path === "/api/settings/email") {
    if (method === "PUT") notifyEmail = data?.notify_email === true;
    return json(res, 200, { notify_email: notifyEmail });
  }
  if (path === "/api/notifications" && method === "GET") return json(res, 200, prelaunchBody("22-notifications-cap-notice"));
  if (path === "/api/notifications/read-all") return json(res, 200, prelaunchBody("24-notifications-read-all"));
  if (path === "/api/templates" && method === "GET") return json(res, 200, { templates });
  if (path === "/api/vocabulary" && method === "GET") return json(res, 200, { terms });
  if (path.startsWith("/api/")) return json(res, 200, {});
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found (mock api)");
});

server.listen(Number(values.port), "127.0.0.1", () => {
  console.log(`mock api on http://127.0.0.1:${values.port}`);
});
