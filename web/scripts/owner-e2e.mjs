#!/usr/bin/env node
// End to end through the Next front door only, against a scratch compose stack (never the pilot):
// log in, create a template, create and delete a vocabulary term, list meetings, load every owner
// page, log out. Vexa and OpenAI are not involved: nothing here sends a bot or rewrites notes.
//
//   node scripts/owner-e2e.mjs --next http://127.0.0.1:8020 --email "$OWNER_EMAIL" --password "$OWNER_PASSWORD"
//
// It writes one template and one term into whichever database FastAPI uses (the term is deleted
// again; the template stays, the API cannot delete one) and exits 1 on the first failed check.
import { parseArgs } from "node:util";
import { randomBytes } from "node:crypto";

const { values } = parseArgs({
  options: {
    next: { type: "string" },
    email: { type: "string" },
    password: { type: "string" },
    json: { type: "boolean", default: false },
  },
});
for (const key of ["next", "email", "password"]) {
  if (!values[key]) {
    console.error(`missing --${key}`);
    process.exit(2);
  }
}
const BASE = values.next.replace(/\/$/, "");
const run = randomBytes(3).toString("hex");
const TEMPLATE = `E2E template ${run}`;
const TERM = `E2E term ${run}`;
const WRONG = `not-it-${randomBytes(4).toString("hex")}`;

const jar = new Map();
let csrf = null;
const results = [];
let failed = 0;

async function send(method, path, { json, headers = {}, cookies = jar } = {}) {
  const cookie = [...cookies].map(([k, v]) => `${k}=${v}`).join("; ");
  const res = await fetch(BASE + path, {
    method,
    redirect: "manual",
    headers: {
      "X-Forwarded-For": "10.30.0.1",
      ...(cookie ? { Cookie: cookie } : {}),
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : undefined,
  });
  for (const line of res.headers.getSetCookie()) {
    const [pair] = line.split(";");
    const i = pair.indexOf("=");
    cookies.set(pair.slice(0, i), pair.slice(i + 1));
  }
  const text = await res.text();
  let body = null;
  try {
    body = JSON.parse(text);
  } catch {
    // HTML
  }
  return { status: res.status, headers: res.headers, text, body };
}

const write = (method, path, json) => send(method, path, { json: json ?? {}, headers: { "X-CSRF-Token": csrf } });

function check(label, ok, detail = "") {
  results.push({ label, ok, detail });
  if (!ok) failed += 1;
  if (!values.json) console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  -> ${detail}`}`);
  return ok;
}
const loc = (r) => r.headers.get("location") ?? "";

// 1. Logged out
let r = await send("GET", "/");
check("GET / logged out: the landing page", r.status === 200 && r.text.includes("Your meetings, turned into finished work."), `status ${r.status}`);
const csp = r.headers.get("content-security-policy") ?? "";
check("GET / has a nonce CSP without unsafe-inline", /nonce-/.test(csp) && !/unsafe-inline|unsafe-eval/.test(csp), csp);
check("GET / offers Sign in, not Open the app", r.text.includes("Sign in") && !r.text.includes("Open the app"));
check("GET / is not cached", /no-store|private/.test(r.headers.get("cache-control") ?? ""), r.headers.get("cache-control"));

for (const path of ["/meetings", "/templates", "/vocabulary", "/meetings/1"]) {
  r = await send("GET", path);
  check(`GET ${path} logged out goes to /login`, [307, 308, 303].includes(r.status) && loc(r).endsWith("/login"), `${r.status} ${loc(r)}`);
}
r = await send("GET", "/styleguide");
check("GET /styleguide logged out is a 404 (owner only)", r.status === 404, `status ${r.status}`);
r = await send("GET", "/login");
check("GET /login: the sign-in page", r.status === 200 && r.text.includes("Welcome back"), `status ${r.status}`);

// 2. Login through the API, through Next
r = await send("GET", "/api/session");
csrf = r.body?.csrf_token;
check("GET /api/session through Next: anonymous, with a token and a cookie", r.body?.authenticated === false && !!csrf && jar.has("session"), JSON.stringify(r.body));
r = await write("POST", "/api/session", { email: values.email, password: WRONG });
check("login with a wrong password: 401 invalid_credentials", r.status === 401 && r.body?.error?.code === "invalid_credentials", `${r.status} ${r.text}`);
r = await write("POST", "/api/session", { email: values.email, password: values.password });
check("login: 200 authenticated, new token and cookie", r.status === 200 && r.body?.authenticated === true && r.body.csrf_token !== csrf && r.headers.getSetCookie().length > 0, `${r.status} ${r.text}`);
check("login: the response names the user and their usage", typeof r.body?.user?.email === "string" && typeof r.body?.usage?.used_seconds === "number", JSON.stringify(r.body));
csrf = r.body?.csrf_token;
const loggedIn = new Map(jar);

// 3. Pages as the owner
r = await send("GET", "/");
check("GET / logged in: the landing page with Open the app (to /meetings), not Sign in", r.status === 200 && r.text.includes("Your meetings, turned into finished work.") && r.text.includes("Open the app") && r.text.includes('href="/meetings"') && !r.text.includes("Get started"), `status ${r.status}`);
check("GET / logged in is not noindex", !/noindex/.test(r.text), "robots meta present");
r = await send("GET", "/welcome");
check("GET /welcome redirects permanently to /", r.status === 308 && (loc(r) === "/" || loc(r).endsWith("/")), `${r.status} ${loc(r)}`);
r = await send("GET", "/meetings");
check("GET /meetings logged in: Meetings, the new-meeting form, a log-out button", r.status === 200 && r.text.includes("Send note taker") && r.text.includes('aria-label="Log out"'), `status ${r.status}`);
check("GET /meetings logged in is noindex", /noindex/.test(r.text), "no robots meta");
r = await send("GET", "/login");
check("GET /login logged in goes to /meetings", [307, 308, 303].includes(r.status) && loc(r).endsWith("/meetings"), `${r.status} ${loc(r)}`);
r = await send("GET", "/styleguide");
check("GET /styleguide logged in: the style guide", r.status === 200 && r.text.includes("Style guide"), `status ${r.status}`);
r = await send("GET", "/privacy");
check("GET /privacy logged in: the policy with its table of contents", r.status === 200 && r.text.includes("ON THIS PAGE"), `status ${r.status}`);

// 4. Template
r = await write("POST", "/api/templates", { name: TEMPLATE, instructions: "One line per person." });
const templateId = r.body?.id;
check("create a template: 201", r.status === 201 && r.body?.name === TEMPLATE, `${r.status} ${r.text}`);
r = await write("POST", "/api/templates", { name: TEMPLATE, instructions: "x" });
check("create it again: 400 duplicate_name", r.status === 400 && r.body?.error?.code === "duplicate_name", `${r.status} ${r.text}`);
r = await write("PUT", `/api/templates/${templateId}`, { name: TEMPLATE, instructions: "Two lines per person." });
check("update the template: 200", r.status === 200 && r.body?.instructions === "Two lines per person.", `${r.status} ${r.text}`);
r = await send("GET", "/templates");
check("GET /templates shows the new template, server rendered", r.status === 200 && r.text.includes(TEMPLATE), `status ${r.status}`);
r = await send("GET", "/meetings");
check("the new-meeting form offers the template", r.text.includes(TEMPLATE));

// 5. Vocabulary
r = await write("POST", "/api/vocabulary", { term: TERM });
const termId = r.body?.id;
check("add a vocabulary term: 201", r.status === 201 && r.body?.term === TERM, `${r.status} ${r.text}`);
r = await send("GET", "/vocabulary");
check("GET /vocabulary shows the term", r.status === 200 && r.text.includes(TERM), `status ${r.status}`);
r = await write("DELETE", `/api/vocabulary/${termId}`);
check("delete the term: 204", r.status === 204, `status ${r.status}`);
r = await write("DELETE", `/api/vocabulary/${termId}`);
check("delete it again: 404 not_found", r.status === 404 && r.body?.error?.code === "not_found", `${r.status} ${r.text}`);

// 6. Meetings
r = await send("GET", "/api/meetings");
const meetings = r.body?.meetings ?? [];
check("list meetings through Next: 200 with the wrapped list", r.status === 200 && Array.isArray(r.body?.meetings), `${r.status} ${r.text.slice(0, 80)}`);
if (meetings.length > 0) {
  const m = meetings[0];
  r = await send("GET", `/meetings/${m.id}`);
  check(`GET /meetings/${m.id} renders the meeting`, r.status === 200 && r.text.includes(m.title), `status ${r.status}`);
  r = await send("GET", "/meetings");
  check("GET /meetings lists the meetings", r.text.includes(m.title));
} else {
  console.log("note  no meetings in this database: meeting pages were not loaded (seed with scripts/seed-owner-states.sql)");
}
r = await send("GET", "/meetings/999999");
check("GET /meetings/<unknown> is a 404 page", r.status === 404, `status ${r.status}`);
r = await send("GET", "/meetings/abc");
check("GET /meetings/<not a number> is a 404", r.status === 404, `status ${r.status}`);
r = await write("POST", "/api/meetings", { url: "https://example.com/x", template_id: templateId });
check("create a meeting with a bad link: 400 invalid_meeting_url (nothing sent to Vexa)", r.status === 400 && r.body?.error?.code === "invalid_meeting_url", `${r.status} ${r.text}`);

// 7. Fallback still serves Jinja
r = await send("GET", "/health");
check("GET /health still reaches FastAPI through Next", r.status === 200 && r.text.trim() === "ok", `${r.status} ${r.text}`);
r = await send("GET", "/static/app.css");
check("GET /static/app.css still reaches FastAPI", r.status === 200 && /css/.test(r.headers.get("content-type") ?? ""), `${r.status}`);

// 8. Logout
r = await send("POST", "/logout", { headers: { "Sec-Fetch-Site": "cross-site" } });
check("POST /logout from another site: 403, still logged in", r.status === 403, `status ${r.status}`);
r = await send("GET", "/templates");
check("... and still logged in", r.status === 200, `status ${r.status}`);
r = await send("POST", "/logout", { headers: { "Sec-Fetch-Site": "same-origin" } });
check("POST /logout: 303 to /login with a new cookie", r.status === 303 && loc(r) === "/login" && r.headers.getSetCookie().length > 0, `${r.status} ${loc(r)}`);
r = await send("GET", "/templates");
check("after logout the browser's new cookie is anonymous", [307, 308, 303].includes(r.status) && loc(r).endsWith("/login"), `${r.status} ${loc(r)}`);
r = await send("GET", "/templates", { cookies: loggedIn });
check("the old cookie is revoked (logout signs this user out on every device)", [307, 308, 303].includes(r.status) && loc(r).endsWith("/login"), `${r.status} ${loc(r)}`);
r = await send("GET", "/api/templates", { cookies: loggedIn });
check("the old cookie gets 401 from the API too", r.status === 401 && r.body?.error?.code === "not_authenticated", `${r.status} ${r.text}`);
r = await send("GET", "/logout");
check("GET /logout never logs out", r.status === 303 && loc(r) === "/", `${r.status} ${loc(r)}`);

console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (values.json) console.log(JSON.stringify(results, null, 2));
process.exit(failed ? 1 : 0);
