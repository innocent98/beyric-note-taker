#!/usr/bin/env node
// Does FastAPI's login limit see the visitor, or the proxy in front of it? Sends 6 wrong passwords
// as visitor A (X-Forwarded-For: 198.51.100.1, as nginx would set it), then one as visitor B
// (198.51.100.2). With the proxy trusted, A is locked out (429) and B is not (401). With the proxy
// NOT trusted, FastAPI sees one address for everybody and B is locked out by A's failures.
//
//   node scripts/xff-check.mjs --url http://127.0.0.1:8020 --expect separate|shared
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { url: { type: "string" }, expect: { type: "string" } } });
if (!values.url || !["separate", "shared"].includes(values.expect ?? "")) {
  console.error("usage: xff-check.mjs --url <base> --expect separate|shared");
  process.exit(2);
}

async function wrongPassword(ip) {
  const jar = new Map();
  const page = await fetch(values.url + "/login", { headers: { "X-Forwarded-For": ip } });
  const cookie = page.headers.getSetCookie()[0].split(";")[0];
  const token = /name="csrf_token" value="([^"]+)"/.exec(await page.text())[1];
  const res = await fetch(values.url + "/login", {
    method: "POST",
    redirect: "manual",
    headers: { "X-Forwarded-For": ip, Cookie: cookie, "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ password: "wrong", csrf_token: token }).toString(),
  });
  void jar;
  return res.status;
}

const a = [];
for (let i = 0; i < 6; i++) a.push(await wrongPassword("198.51.100.1"));
const b = await wrongPassword("198.51.100.2");
console.log(`visitor A, 6 wrong passwords: ${a.join(",")}`);
console.log(`visitor B, first wrong password: ${b}`);
const separate = a.at(-1) === 429 && b === 401;
const shared = a.at(-1) === 429 && b === 429;
const ok = values.expect === "separate" ? separate : shared;
console.log(`${ok ? "PASS" : "FAIL"}  limit is ${separate ? "per visitor" : shared ? "shared by everyone (one address seen)" : "unclear"}; expected ${values.expect}`);
process.exit(ok ? 0 : 1);
