#!/usr/bin/env node
// Drives the owner app in a real Chromium, light and dark, at 375 px and 1280 px: logs in through
// the form, opens every screen and every meeting state, adds a template and a term through the UI,
// logs out, and takes a screenshot of each. Fails on a console error or warning, a CSP violation,
// horizontal scrolling at 375 px, or a screen that does not show what it should.
//
//   PLAYWRIGHT=/path/to/playwright/index.mjs node scripts/owner-browser-check.mjs \
//     --next http://127.0.0.1:8020 --email "$OWNER_EMAIL" --password "$OWNER_PASSWORD" --shots /tmp/shots [--send-check] [--poll-check]
//
// Run it against a scratch stack seeded with scripts/seed-owner-states.sql. The seed has a meeting
// in "processing", which locks the new-meeting form (a bot is already on a meeting); --send-check
// needs that form open, so run it after setting those rows to failed. --poll-check opens the
// processing meeting and waits for the page's own 30-second refresh request.
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const { values } = parseArgs({
  options: {
    next: { type: "string" },
    email: { type: "string" },
    password: { type: "string" },
    shots: { type: "string" },
    "send-check": { type: "boolean", default: false },
    "poll-check": { type: "boolean", default: false },
  },
});
for (const key of ["next", "email", "password", "shots"]) {
  if (!values[key]) {
    console.error(`missing --${key}`);
    process.exit(2);
  }
}
const BASE = values.next.replace(/\/$/, "");
mkdirSync(values.shots, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch();
let failed = 0;
const tag = Math.random().toString(36).slice(2, 7);

function check(label, ok, detail = "") {
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  -> ${detail}`}`);
}

for (const scheme of ["light", "dark"]) {
  for (const [width, height] of [[375, 812], [1280, 800]]) {
    const name = `${scheme}-${width}`;
    const context = await browser.newContext({ colorScheme: scheme, viewport: { width, height } });
    const page = await context.newPage();
    const problems = [];
    page.on("console", (msg) => {
      if (!["error", "warning"].includes(msg.type())) return;
      // The browser logs the 401 and 400 this script provokes on purpose (a wrong password, a
      // duplicate template name) as console errors; anything else is a real problem.
      if (/Failed to load resource.*status of 40[01]\b/.test(msg.text())) return;
      // Chromium warns about the font preload in app/layout.tsx (stream A) on any page left open
      // for a few seconds, /privacy included. Not an owner-screen problem: listed in the SOP.
      if (/was preloaded using link preload but not used/.test(msg.text())) return;
      problems.push(`console ${msg.type()}: ${msg.text()}`);
    });
    page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
    await context.addInitScript(() => {
      document.addEventListener("securitypolicyviolation", (e) =>
        console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`),
      );
    });
    const shot = (label) => page.screenshot({ path: join(values.shots, `${name}-${label}.png`), fullPage: true });
    const noScroll = async (label) =>
      check(`${name} ${label}: no horizontal scroll`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));

    // Logged out
    await page.goto(`${BASE}/`);
    await page.getByRole("link", { name: "Sign in" }).first().waitFor();
    await shot("landing");
    await noScroll("landing");

    // Login through the form (wrong password first)
    await page.goto(`${BASE}/login`);
    await shot("login");
    await page.getByLabel("Work email").fill(values.email);
    await page.getByLabel("Password").fill(`wrong-${tag}`);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("alert").filter({ hasText: "That email or password is not right." }).waitFor();
    check(`${name} login: wrong password shows the API's message`, true);
    await shot("login-error");
    await page.getByLabel("Password").fill(values.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: /Send note taker/ }).waitFor();
    check(`${name} login: signed in and landed on the Meetings page`, new URL(page.url()).pathname === "/meetings");
    await shot("meetings");
    await noScroll("meetings");

    // Meetings and each state
    const list = await (await context.request.get(`${BASE}/api/meetings`)).json();
    const seen = new Set();
    for (const m of list.meetings.slice(0, 8)) {
      const key = `${m.id}-${m.status}-${m.step}`;
      seen.add(key);
      await page.goto(`${BASE}/meetings/${m.id}`);
      await page.getByRole("heading", { level: 1, name: m.title }).waitFor();
      await shot(`meeting-${key}`);
      await noScroll(`meeting ${key}`);
      if (m.status === "ready") {
        const tab = page.getByRole("tab", { name: "Transcript" });
        if (await tab.count()) {
          await tab.click();
          await shot(`meeting-${key}-transcript`);
        }
      }
    }
    check(`${name} meeting pages: ${[...seen].join(", ") || "none seeded"}`, seen.size > 0, "no meetings; seed the database");

    // Client navigation keeps the document (no full load) between Next pages
    await page.goto(`${BASE}/meetings`);
    await page.evaluate(() => (window.__alive = true));
    await page.getByRole("link", { name: "Templates" }).first().click();
    await page.getByRole("heading", { level: 1, name: "Templates" }).waitFor();
    check(`${name} client navigation to /templates keeps the page`, await page.evaluate(() => window.__alive === true));
    await shot("templates");
    await noScroll("templates");

    // Template through the UI
    const templateName = `UI ${name} ${tag}`;
    await page.getByRole("button", { name: "Add template" }).first().click();
    await page.getByLabel("Name").fill(templateName);
    await page.getByLabel("Instructions").fill("Written by the browser check.");
    await page.getByRole("button", { name: "Add template" }).last().click();
    await page.getByRole("status").filter({ hasText: "Saved" }).waitFor();
    check(`${name} templates: added through the UI and shows Saved`, true);
    await shot("templates-saved");
    await page.getByRole("button", { name: "Add template" }).first().click();
    await page.getByLabel("Name").fill(templateName);
    await page.getByLabel("Instructions").fill("again");
    await page.getByRole("button", { name: "Add template" }).last().click();
    await page.getByRole("alert").filter({ hasText: "A template with that name already exists." }).waitFor();
    check(`${name} templates: a duplicate name shows the API's message`, true);
    await shot("templates-error");

    // Vocabulary through the UI
    await page.getByRole("link", { name: "Vocabulary" }).first().click();
    await page.getByRole("heading", { level: 1, name: "Vocabulary" }).waitFor();
    const term = `Term ${name} ${tag}`;
    await page.getByLabel("New term").fill(term);
    await page.getByRole("button", { name: "Add term" }).click();
    await page.getByText(term, { exact: true }).waitFor();
    check(`${name} vocabulary: term added through the UI`, true);
    await shot("vocabulary");
    await noScroll("vocabulary");
    await page.getByRole("button", { name: `Delete ${term}` }).click();
    await page.getByText(term, { exact: true }).waitFor({ state: "detached" });
    check(`${name} vocabulary: term deleted through the UI`, true);

    if (values["send-check"] && name === "light-1280") {
      await page.goto(`${BASE}/meetings`);
      await page.getByLabel("Meeting link").fill("https://example.com/not-a-meeting");
      await page.getByRole("button", { name: "Send note taker" }).click();
      await page.getByRole("alert").filter({ hasText: "That is not a Google Meet, Zoom or Microsoft Teams link" }).waitFor();
      check(`${name} new meeting: a bad link shows the API's message and keeps the input`, (await page.getByLabel("Meeting link").inputValue()) === "https://example.com/not-a-meeting");
      await shot("meetings-error");
    }

    if (values["poll-check"] && name === "light-1280") {
      const working = list.meetings.find((m) => m.status === "processing");
      if (working) {
        const started = Date.now();
        await page.goto(`${BASE}/meetings/${working.id}`);
        await page.waitForRequest((req) => new URL(req.url()).pathname === `/api/meetings/${working.id}` && req.method() === "GET", { timeout: 45_000 });
        check(`${name} meeting ${working.id}: refreshed itself after ${Math.round((Date.now() - started) / 1000)} s (expect about 30)`, true);
      } else {
        check(`${name} poll check: a processing meeting exists`, false, "seed one");
      }
    }

    // Logout through the button
    await page.goto(`${BASE}/meetings`);
    await page.getByRole("button", { name: "Log out" }).click();
    await page.waitForURL(`${BASE}/login`);
    check(`${name} logout: back on the login page`, true);
    await page.goto(`${BASE}/templates`);
    await page.waitForURL(`${BASE}/login`);
    check(`${name} logout: the owner pages send a logged-out visitor to /login`, true);

    check(`${name}: no console errors or warnings, no CSP violations`, problems.length === 0, problems.join(" | "));
    await context.close();
  }
}
await browser.close();
console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
