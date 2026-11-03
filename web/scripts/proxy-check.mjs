#!/usr/bin/env node
// Proves the Next.js fallback proxy is transparent for the Jinja pages. Every step is sent twice,
// once through Next (--next) and once straight to FastAPI (--direct), and the two responses must
// agree on status, redirect target, headers that matter, Set-Cookie attributes and body.
//
//   node scripts/proxy-check.mjs --next http://127.0.0.1:8020 --direct http://127.0.0.1:8021 \
//        --password "$OWNER_PASSWORD"
//
// It logs in and creates two templates (one per side) in whichever database FastAPI uses, so run
// it against a scratch stack, never the pilot. It creates nothing with Vexa.
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    next: { type: "string" },
    direct: { type: "string" },
    password: { type: "string" },
    json: { type: "boolean", default: false },
  },
});
for (const k of ["next", "direct", "password"]) {
  if (!values[k]) {
    console.error(`missing --${k}`);
    process.exit(2);
  }
}

// Different "visitor addresses" (what nginx would put in X-Forwarded-For) per side, so the login
// limit counts them separately and the check also proves the address survives the extra hop.
// Never the real password; random so it cannot be mistaken for a credential.
const BAD_GUESS = `not-it-${Math.random().toString(36).slice(2)}`;

const SIDES = [
  { name: "next", base: values.next.replace(/\/$/, ""), ip: "10.20.0.1", tag: "via-next" },
  { name: "direct", base: values.direct.replace(/\/$/, ""), ip: "10.20.0.2", tag: "direct" },
];
for (const s of SIDES) s.jar = new Map();

async function send(side, method, path, { form, headers = {}, jar = side.jar } = {}) {
  const cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
  const init = {
    method,
    redirect: "manual",
    headers: { "X-Forwarded-For": side.ip, ...(cookie ? { Cookie: cookie } : {}), ...headers },
  };
  if (form) {
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
    init.body = new URLSearchParams(form).toString();
  }
  const res = await fetch(side.base + path, init);
  const setCookie = res.headers.getSetCookie();
  for (const line of setCookie) {
    const [pair] = line.split(";");
    const i = pair.indexOf("=");
    jar.set(pair.slice(0, i), pair.slice(i + 1));
  }
  const body = Buffer.from(await res.arrayBuffer());
  return { status: res.status, headers: res.headers, setCookie, body };
}

const csrfIn = (html) => /name="csrf_token" value="([^"]+)"/.exec(html)?.[1];
const normalise = (text) => text.replace(/(name="csrf_token" value=")[^"]+/g, "$1<csrf>");
// Set-Cookie compared by name and attributes, not by the signed value.
const cookieShape = (line) =>
  line
    .split(";")
    .map((p, i) => (i === 0 ? p.split("=")[0] : p.trim().toLowerCase()))
    .join("; ");
const KEEP = ["content-type", "location", "content-security-policy", "cache-control", "x-content-type-options", "referrer-policy"];

const results = [];
let failed = 0;

async function step(label, make, { body = true } = {}) {
  const [a, b] = await Promise.all(SIDES.map((s) => make(s)));
  const problems = [];
  if (a.status !== b.status) problems.push(`status ${a.status} vs ${b.status}`);
  for (const h of KEEP) {
    if (a.headers.get(h) !== b.headers.get(h)) problems.push(`${h}: ${a.headers.get(h)} vs ${b.headers.get(h)}`);
  }
  const ca = a.setCookie.map(cookieShape).join(" | ");
  const cb = b.setCookie.map(cookieShape).join(" | ");
  if (ca !== cb) problems.push(`set-cookie: ${ca} vs ${cb}`);
  if (body && normalise(a.body.toString()) !== normalise(b.body.toString())) problems.push("body differs");
  if (!body && a.body.length === 0 !== (b.body.length === 0)) problems.push("body emptiness differs");
  const ok = problems.length === 0;
  if (!ok) failed++;
  results.push({ label, ok, status: a.status, location: a.headers.get("location"), setCookie: ca, csp: a.headers.get("content-security-policy"), problems });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}  ->  ${a.status}${a.headers.get("location") ? " " + a.headers.get("location") : ""}${ca ? "  [Set-Cookie: " + ca + "]" : ""}`);
  for (const p of problems) console.log(`        ${p}`);
  return [a, b];
}

// Each side runs its own request, in step, with its own cookie jar.
const each = (fn) => (side) => fn(side);

// 1. A fresh visitor gets a login page and a session cookie.
const [login0] = await step("GET /login (new visitor): page, CSRF token and Set-Cookie", each((s) => send(s, "GET", "/login")));
const tokens = new Map(SIDES.map((s) => [s.name, null]));
for (const s of SIDES) {
  // read each side's token from its own page
  const r = await send(s, "GET", "/login");
  tokens.set(s.name, csrfIn(r.body.toString()));
}
if (!tokens.get("next") || !tokens.get("direct")) {
  console.log("FAIL  no CSRF token found on /login");
  process.exit(1);
}
void login0;

// 2. Protected pages redirect (303) to /login, and the Location is passed through.
await step("GET /templates without a login: 303 to /login", each((s) => send(s, "GET", "/templates", { jar: new Map() })));

// 3. A POST with no CSRF token is refused with the app's own 403 page.
await step("POST /login without a CSRF token: 403", each((s) => send(s, "POST", "/login", { form: { password: BAD_GUESS } })));

// 4. A wrong password, with a token: the form comes back with an error.
await step("POST /login wrong password: error page", each((s) => send(s, "POST", "/login", { form: { password: BAD_GUESS, csrf_token: tokens.get(s.name) } })));

// 5. The right password: 303 to /, a fresh session cookie.
await step("POST /login correct password: 303 to / and a new session cookie", each((s) => send(s, "POST", "/login", { form: { password: values.password, csrf_token: tokens.get(s.name) } })), { body: false });

// 6. Logged in: pages render with the cookie forwarded.
await step("GET /templates logged in: 200", each((s) => send(s, "GET", "/templates")));
await step("GET / logged in: the meetings page", each((s) => send(s, "GET", "/")));
for (const s of SIDES) {
  const r = await send(s, "GET", "/templates");
  tokens.set(s.name, csrfIn(r.body.toString()));
}

// 7. A POST with a form body: creates a template and 303s back to /templates.
await step("POST /templates (form body, CSRF): 303 to /templates", each((s) => send(s, "POST", "/templates", { form: { name: `Proxy check ${Date.now()} ${s.tag}`, instructions: "Created by web/scripts/proxy-check.mjs.", csrf_token: tokens.get(s.name) } })), { body: false });

// 8. Same name again: the app's own validation error comes back unchanged (400).
const dup = `Proxy check duplicate ${Date.now()}`;
for (const s of SIDES) await send(s, "POST", "/templates", { form: { name: `${dup} ${s.tag}`, instructions: "x", csrf_token: tokens.get(s.name) } });
await step("POST /templates with a name that exists: 400 with the app's message", each((s) => send(s, "POST", "/templates", { form: { name: `${dup} ${s.tag}`, instructions: "x", csrf_token: tokens.get(s.name) } })), { body: false });

// 9. A large body (nginx allows 1 MB) reaches FastAPI whole: it answers with its own length error.
await step("POST /templates with a 300 KB body: FastAPI's own 400, not a proxy error", each((s) => send(s, "POST", "/templates", { form: { name: `Big ${s.tag}`, instructions: "x".repeat(300_000), csrf_token: tokens.get(s.name) } })), { body: false });

// 10. Static files, health and unknown paths fall through.
await step("GET /static/tokens.css: same bytes and headers", each((s) => send(s, "GET", "/static/tokens.css")));
await step("GET /health: ok", each((s) => send(s, "GET", "/health")));
await step("GET /does-not-exist: FastAPI's 404", each((s) => send(s, "GET", "/does-not-exist")));
await step("GET /meetings/999999 with a query string", each((s) => send(s, "GET", "/meetings/999999?a=1&b=two")), { body: false });

// 11. The login limit is keyed by the address in X-Forwarded-For, so it must see the visitor, not
// Next: six wrong passwords from one address give the same answers on both sides (401 x5, then 429).
const limitStatuses = { next: [], direct: [] };
for (let i = 0; i < 6; i++) {
  for (const s of SIDES) {
    const jar = new Map();
    const page = await send(s, "GET", "/login", { jar, headers: { "X-Forwarded-For": `10.30.0.${s.name === "next" ? 1 : 2}` } });
    const r = await send(s, "POST", "/login", { jar, form: { password: BAD_GUESS, csrf_token: csrfIn(page.body.toString()) }, headers: { "X-Forwarded-For": `10.30.0.${s.name === "next" ? 1 : 2}` } });
    limitStatuses[s.name].push(r.status);
  }
}
const same = JSON.stringify(limitStatuses.next) === JSON.stringify(limitStatuses.direct);
const locked = limitStatuses.next.includes(429);
if (!same || !locked) failed++;
console.log(`${same && locked ? "PASS" : "FAIL"}  login limit counts the visitor's address, not Next's:  via Next ${limitStatuses.next.join(",")}  direct ${limitStatuses.direct.join(",")}`);
results.push({ label: "login limit by visitor address", ok: same && locked, via_next: limitStatuses.next, direct: limitStatuses.direct });

// 12. Logout 303s to /login.
await step("POST /logout: 303 to /login", each((s) => send(s, "POST", "/logout", { form: { csrf_token: tokens.get(s.name) } })), { body: false });

console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (values.json) console.log(JSON.stringify(results, null, 2));
process.exit(failed ? 1 : 0);
