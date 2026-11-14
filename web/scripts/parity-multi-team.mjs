#!/usr/bin/env node
// Screenshots the multi-team screens (stage 3) at 1440, 768 and 390 px, one JPEG per state and width.
// The designer's file has NONE of these (the team switcher, the Teams screen, the meeting page's team
// card and its Move dialog are lead-designed), so there is nothing to put on the left: the shots are
// for the designer to review. The screens the design DOES cover are shot with the switch OFF through
// `parity-shots.mjs` (design left, page right), to show they are unchanged; see docs/ui/parity-report.md.
//
//   node scripts/mock-api.mjs --port 8031 --multi-team on
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run build && PORT=3100 node .next/standalone/server.js
//   PLAYWRIGHT=/path/to/playwright-core/index.mjs CHROME=/path/to/chrome \
//     node scripts/parity-multi-team.mjs --next http://127.0.0.1:3100 --out docs/ui/parity/after
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({ options: { next: { type: "string" }, out: { type: "string" }, only: { type: "string" } } });
const NEXT = values.next.replace(/\/$/, "");
const WIDTHS = [1440, 768, 390];
mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

async function shot(name, width, run) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.setDefaultTimeout(20000);
  await run(page);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${values.out}/app-multi-team-${name}-${width}.jpg`, type: "jpeg", quality: 82, fullPage: false });
  console.log(`shot ${name} ${width}${problems.length ? `: ${problems.join(" | ")}` : ""}`);
  await context.close();
}

const go = (path) => async (page) => {
  // "load", not "networkidle": a page that polls (the meeting page, the task counts) is never idle.
  await page.goto(`${NEXT}${path}`, { waitUntil: "load" });
  await page.waitForTimeout(900);
};
// On a phone the sidebar is a drawer; the switcher is in the top bar, so nothing to open.
const readyMeeting = async (page) => {
  const list = await (await page.request.get(`${NEXT}/api/meetings`)).json();
  return list.meetings.find((m) => m.status === "ready").id;
};
const csrf = async (page) => (await (await page.request.get(`${NEXT}/api/session`)).json()).csrf_token;

const STATES = {
  // The switcher on the Meetings list (the shell on every page).
  "switcher-meetings": go("/"),
  // The same switcher opened is the system's own select: a screenshot of the closed one plus the Team page on a team.
  "team-personal": go("/team?team=1"),
  "team-ordinary": go("/team?team=2"),
  "teams-list": go("/teams"),
  "settings-personal": go("/settings?team=1"),
  "settings-ordinary": go("/settings?team=2"),
  "meeting-private": async (page) => {
    await go(`/meetings/${await readyMeeting(page)}`)(page);
  },
  "meeting-move-dialog": async (page) => {
    await go(`/meetings/${await readyMeeting(page)}`)(page);
    await page.getByRole("button", { name: "Move to team" }).click();
    await page.waitForTimeout(300);
  },
  "meeting-moved": async (page) => {
    const id = await readyMeeting(page);
    await page.request.post(`${NEXT}/api/meetings/${id}/move`, { data: { team_id: 2 }, headers: { "X-CSRF-Token": await csrf(page) } });
    await go(`/meetings/${id}`)(page);
  },
  "meeting-move-back-dialog": async (page) => {
    const id = await readyMeeting(page);
    await page.request.post(`${NEXT}/api/meetings/${id}/move`, { data: { team_id: 2 }, headers: { "X-CSRF-Token": await csrf(page) } });
    await go(`/meetings/${id}`)(page);
    await page.getByRole("button", { name: "Move back to you" }).click();
    await page.waitForTimeout(300);
  },
  "teams-limit": async (page) => {
    const token = await csrf(page);
    for (const name of ["Side project", "Book club"]) await page.request.post(`${NEXT}/api/teams`, { data: { name }, headers: { "X-CSRF-Token": token } });
    await go("/teams")(page);
    await page.getByLabel("Team name").evaluate((el) => el.removeAttribute("disabled"));
  },
};

const only = values.only?.split(",");
for (const [name, run] of Object.entries(STATES)) {
  if (only && !only.includes(name)) continue;
  for (const width of WIDTHS) {
    try {
      await shot(name, width, run);
    } catch (error) {
      console.log(`${name} ${width}: FAILED ${error.message.split("\n")[0]}`);
    }
  }
  console.log("done", name);
}
await browser.close();
