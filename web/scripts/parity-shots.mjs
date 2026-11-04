#!/usr/bin/env node
// Screenshots each screen twice, the designer's prototype and the Next page, at 1440, 768 and 390
// px, and joins them into one side-by-side JPEG per screen and width (design on the left). This is
// how docs/ui/parity-report.md was checked.
//
//   # the designer's files, served read-only
//   python3 -m http.server 8700 --directory "docs/ui/Beyric Note Taker redesign"
//   # the owner screens need an API: the mock replays the captures (no Docker, no database)
//   node scripts/mock-api.mjs --port 8031
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run build && PORT=3100 node .next/standalone/server.js
//
//   PLAYWRIGHT=/path/to/playwright/index.mjs [CHROME=/path/to/chrome] node scripts/parity-shots.mjs \
//     --design http://127.0.0.1:8700 --next http://127.0.0.1:3100 --out /tmp/parity [--only landing,signin]
//
// The prototype of the main app is one stateful page, so its screens are reached by clicking the
// same way a person would. Screens with no prototype (error and empty states) are shot alone.
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({
  options: { design: { type: "string" }, next: { type: "string" }, out: { type: "string" }, only: { type: "string" } },
});
for (const key of ["design", "next", "out"]) {
  if (!values[key]) {
    console.error(`missing --${key}`);
    process.exit(2);
  }
}
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const APP = `${DESIGN}/Beyric%20Note%20Taker.dc.html`;
const WIDTHS = [1440, 768, 390];

// name: [design url, design clicks, next path, signed in, clicks on the Next page (optional)]
const SCREENS = {
  landing: [`${DESIGN}/Landing.dc.html`, [], "/", false],
  signin: [`${DESIGN}/Auth.dc.html#sign-in`, [], "/login", false],
  privacy: [`${DESIGN}/Privacy%20Policy.dc.html`, [], "/privacy", false],
  terms: [`${DESIGN}/Terms%20of%20Service.dc.html`, [], "/terms", false],
  "app-meetings": [APP, [], "/", true],
  "app-detail": [APP, [["Beyric Monday planning", "last"]], "/meetings/1", true],
  "app-templates": [APP, [["Templates", "first"]], "/templates", true],
  "app-vocabulary": [APP, [["Vocabulary", "first"]], "/vocabulary", true],
  // The two-step ask the design does not have (it deletes at once): Next alone, after pressing the button.
  "app-templates-ask": [null, [], "/templates", true, [["Delete template", "first"]]],
  // The calendar: the design's cal state is set by pressing "Connect Google Calendar" (it answers
  // after 1 s), so those clicks wait. The Next side shows what the mock's --calendar flag says.
  "app-settings": [APP, [["Settings", "first"]], "/settings", true],
  "app-settings-connected": [APP, [["Settings", "first"], ["Connect Google Calendar", "first", 1500]], "/settings", true],
  "app-meetings-calendar": [APP, [["Settings", "first"], ["Connect Google Calendar", "first", 1500], ["Meetings", "first"]], "/", true],
  "app-detail-processing": [null, [], "/meetings/2", true],
  "app-detail-waiting": [null, [], "/meetings/3", true],
  "app-detail-failed": [null, [], "/meetings/4", true],
  "app-meeting-missing": [null, [], "/meetings/999", true],
};

mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const only = values.only?.split(",");

async function shoot(url, width, clicks, signedIn) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  // The Meetings list polls while a meeting is being worked on, so the network may never go idle.
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
  for (const [text, which, wait] of clicks) {
    const match = page.getByText(text, { exact: true });
    // A click from script, because on a phone the desktop-only prototype clips its own controls.
    await (which === "last" ? match.last() : match.first()).evaluate((el) => el.click());
    await page.waitForTimeout(wait ?? 400);
  }
  const png = await page.screenshot({ fullPage: true });
  await context.close();
  return png;
}

for (const [name, [designUrl, clicks, path, signedIn, nextClicks = []]] of Object.entries(SCREENS)) {
  if (only && !only.includes(name)) continue;
  for (const width of WIDTHS) {
    const next = await shoot(NEXT + path, width, nextClicks, signedIn);
    const design = designUrl ? await shoot(designUrl, width, clicks, false) : null;
    const images = [design, next].filter(Boolean).map((png) => `data:image/png;base64,${png.toString("base64")}`);
    const total = images.length * width + (images.length - 1) * 30;
    const zoom = Math.min(1, 1500 / total);
    const sheet = await browser.newPage({ viewport: { width: Math.round(total * zoom), height: 900 } });
    await sheet.setContent(
      `<body style="margin:0;background:#888;zoom:${zoom};display:flex;gap:30px">${images
        .map((src) => `<img style="display:block;width:${width}px" src="${src}">`)
        .join("")}</body>`,
    );
    await sheet.screenshot({ path: `${values.out}/${name}-${width}.jpg`, fullPage: true, type: "jpeg", quality: 62 });
    await sheet.close();
    console.log(`${name} ${width}`);
  }
}
await browser.close();
