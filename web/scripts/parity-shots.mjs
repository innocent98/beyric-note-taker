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
//     --design http://127.0.0.1:8700 --next http://127.0.0.1:3100 --out /tmp/parity [--only landing,signin] \
//     [--mock http://127.0.0.1:8031]
//
// `--mock` is the mock API's own address: before each Next shot of a screen that changes state (the
// translation screens) it is asked to start again from nothing (`/__mock/reset`).
//
// The prototype of the main app is one stateful page, so its screens are reached by clicking the
// same way a person would. Screens with no prototype (error and empty states) are shot alone.
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({
  options: {
    design: { type: "string" },
    next: { type: "string" },
    out: { type: "string" },
    only: { type: "string" },
    mock: { type: "string" },
  },
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

// A step is `[text, which, wait]` (a click on the first or last element with that text: design side
// only) or an object, run on both sides: `{ select, design, next, wait }` picks the option labelled
// `select` in the select that holds `design` (the prototype) or is named `next` (the page);
// `{ text, wait }` clicks the element with that text. The designer's translation takes 900 ms.
const LANG = (label, wait) => ({ select: label, design: "English (original)", next: "Language", wait });
const OPEN_ZOOM = ["Zoom 88205679674", "first"];
const DEFAULT_LANG = (label, wait) => ({ select: label, design: "Original (no translation)", next: "Default notes language", wait });

// name: [design url, design clicks, next path, signed in, next steps (same objects as the design's)]
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
  "app-templates-ask": [null, [], "/templates", true, [{ text: "Delete template" }]],
  // The calendar: the design's cal state is set by pressing "Connect Google Calendar" (it answers
  // after 1 s), so those clicks wait. The Next side shows what the mock's --calendar flag says.
  "app-settings": [APP, [["Settings", "first"]], "/settings", true],
  "app-settings-connected": [APP, [["Settings", "first"], ["Connect Google Calendar", "first", 1500]], "/settings", true],
  "app-meetings-calendar": [APP, [["Settings", "first"], ["Connect Google Calendar", "first", 1500], ["Meetings", "first"]], "/", true],
  "app-detail-processing": [null, [], "/meetings/2", true],
  "app-detail-waiting": [null, [], "/meetings/3", true],
  "app-detail-failed": [null, [], "/meetings/4", true],
  "app-meeting-missing": [null, [], "/meetings/999", true],
  // Translation (W7): the designer's sample meeting, "Zoom 88205679674", is meeting 8 in the mock.
  "app-translate-original": [APP, [OPEN_ZOOM], "/meetings/8", true],
  "app-translate-working": [APP, [OPEN_ZOOM, LANG("French", 150)], "/meetings/8", true, [LANG("French", 300)]],
  "app-translate-fr": [APP, [OPEN_ZOOM, LANG("French", 1500)], "/meetings/8", true, [LANG("French", 3800)]],
  "app-translate-fr-transcript": [
    APP,
    [OPEN_ZOOM, LANG("French", 1500), ["Transcript", "first", 300]],
    "/meetings/8",
    true,
    [LANG("French", 3800), { text: "Transcript", wait: 300 }],
  ],
  "app-translate-es": [APP, [OPEN_ZOOM, LANG("Spanish", 1500)], "/meetings/8", true, [LANG("Spanish", 3800)]],
  "app-translate-download": [
    APP,
    [OPEN_ZOOM, LANG("French", 1500), ["Download", "first", 300]],
    "/meetings/8",
    true,
    [LANG("French", 3800), { text: "Download", wait: 300 }],
  ],
  "app-settings-language": [APP, [["Settings", "first"], DEFAULT_LANG("French", 300)], "/settings", true, [DEFAULT_LANG("French", 400)]],
};

mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const only = values.only?.split(",");

async function reset() {
  if (values.mock) await fetch(`${values.mock.replace(/\/$/, "")}/__mock/reset`).catch(() => {});
}

async function shoot(url, width, clicks, signedIn, side = "design") {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  // The Meetings list polls while a meeting is being worked on, so the network may never go idle.
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
  for (const step of clicks) {
    if (!Array.isArray(step)) {
      if (step.select) {
        const field =
          side === "design"
            ? page.locator("select", { hasText: step.design })
            : page.getByRole("combobox", { name: step.next });
        await field.first().selectOption({ label: step.select });
      } else {
        await page.getByText(step.text, { exact: true }).first().evaluate((el) => el.click());
      }
      await page.waitForTimeout(step.wait ?? 400);
      continue;
    }
    const [text, which, wait] = step;
    const match = page.getByText(text, { exact: true });
    // A click from script, because on a phone the desktop-only prototype clips its own controls.
    await (which === "last" ? match.last() : match.first()).evaluate((el) => el.click());
    await page.waitForTimeout(wait ?? 400);
  }
  const png = await page.screenshot({ fullPage: true });
  await context.close();
  return png;
}

for (const [name, [designUrl, clicks, path, signedIn, nextSteps = []]] of Object.entries(SCREENS)) {
  if (only && !only.includes(name)) continue;
  for (const width of WIDTHS) {
    await reset();
    const next = await shoot(NEXT + path, width, nextSteps, signedIn, "next");
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
