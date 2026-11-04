#!/usr/bin/env node
// Parity check for the Email section of Settings against the designer's Settings screen
// (docs/ui/Beyric Note Taker redesign/Beyric Note Taker.dc.html). The design has no Email section,
// so the section is compared with the designer's own pattern: the Tasks section ("Create tasks from
// action items" and its switch) and the last row of "Sprints and reminders" (the row padded 14 22 20).
// It prints, for each pair of the same part, the computed styles on both sides and exits 1 when any
// differ, then writes one side-by-side JPEG per width (design on the left).
//
//   python3 -m http.server 8700 --directory "docs/ui/Beyric Note Taker redesign"
//   node scripts/mock-api.mjs --port 8031
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run build && PORT=3100 node .next/standalone/server.js
//   PLAYWRIGHT=/path/to/playwright-core/index.mjs CHROME=/path/to/chrome \
//     node scripts/parity-settings-email.mjs --design http://127.0.0.1:8700 --next http://127.0.0.1:3100 --out docs/ui/parity/after
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" }, out: { type: "string" } } });
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const WIDTHS = [1440, 768, 390];
const PROPS = ["fontSize", "fontWeight", "lineHeight", "color", "backgroundColor", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "marginTop", "borderTopWidth", "borderTopColor", "borderRadius", "width", "height", "letterSpacing", "fontFamily"];

mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

async function open(url, width, signedIn, clicks = []) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  await context.addInitScript(() => {
    window.byText = (text) => [...document.querySelectorAll("div,span,label,strong")].find((e) => e.childElementCount === 0 && e.textContent.trim() === text && e.closest("section"));
  });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  for (const text of clicks) {
    await page.getByText(text, { exact: true }).first().evaluate((el) => el.click());
    await page.waitForTimeout(400);
  }
  return { context, page };
}

// Styles of the element a probe function finds, and its box relative to its section.
const measure = (page, find) =>
  page.evaluate(
    ([findSrc, props]) => {
      const el = new Function("return (" + findSrc + ")")()();
      if (!el) return null;
      const cs = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const section = el.closest("section");
      const sr = section.getBoundingClientRect();
      const out = Object.fromEntries(props.map((p) => [p, cs[p]]));
      out.x = Math.round(rect.left - sr.left);
      out.y = Math.round(rect.top - sr.top);
      return out;
    },
    [typeof find === "string" ? find : find.toString(), PROPS],
  );

// `window.byText` is added to every page (see open()), because a finder runs inside the page.
const byText = (text) => `() => window.byText(${JSON.stringify(text)})`;
const designPairs = {
  // the part: [design finder, next finder]
  "section card": [() => window.byText("Create tasks from action items").closest("section"), () => document.querySelector("section#email-preferences")],
  "section title": [byText("Tasks"), byText("Email")],
  "section description": [byText("What happens to action items after a meeting ends."), byText("What the note taker emails you.")],
  "row label": [byText("Create tasks from action items"), byText("Email me when my notes are ready")],
  "row description": [byText("Each action item becomes a task assigned to the teammate named as its owner."), () => document.querySelector("#notify-email-desc")],
  "switch (on)": [
    () => window.byText("Create tasks from action items").parentElement.parentElement.lastElementChild,
    () => document.querySelector("button[role=switch]"),
  ],
  "switch knob": [
    () => window.byText("Create tasks from action items").parentElement.parentElement.lastElementChild.firstElementChild,
    () => document.querySelector("button[role=switch] > span"),
  ],
};
// the designer's last row of a section (padding 14 22 20) against ours
const lastRow = {
  "last row": [() => window.byText("Remind attendees before recurring meetings").parentElement.parentElement, () => document.querySelector("section#email-preferences .set-row")],
};

let failures = 0;
const dpage = await open(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, 1440, false, ["Settings"]);
const npage = await open(`${NEXT}/settings`, 1440, true);
for (const [name, [d, n]] of Object.entries({ ...designPairs, ...lastRow })) {
  const [a, b] = [await measure(dpage.page, d), await measure(npage.page, n)];
  if (!a || !b) {
    console.log(`MISSING  ${name}: design=${!!a} next=${!!b}`);
    failures++;
    continue;
  }
  const diffs = Object.keys(a).filter((k) => a[k] !== b[k] && !["fontFamily"].includes(k) && !(name.startsWith("switch") && k === "borderTopColor") && !(k === "width" && (name.startsWith("section") || name.startsWith("row")))  /* a text block is as wide as its words */  && !(k === "height" && !name.startsWith("switch")) && !(["x", "y"].includes(k) && name !== "section card"));
  if (!diffs.length) console.log(`same     ${name}`);
  else {
    failures++;
    console.log(`DIFFERS  ${name}`);
    for (const k of diffs) console.log(`           ${k}: design ${a[k]} | next ${b[k]}`);
  }
}
await dpage.context.close();
await npage.context.close();

for (const width of WIDTHS) {
  const shoot = async (url, signedIn, clicks) => {
    const { context, page } = await open(url, width, signedIn, clicks);
    const png = await page.screenshot({ fullPage: true });
    await context.close();
    return `data:image/png;base64,${png.toString("base64")}`;
  };
  const images = [await shoot(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, false, ["Settings"]), await shoot(`${NEXT}/settings`, true, [])];
  const total = 2 * width + 30;
  const zoom = Math.min(1, 1500 / total);
  const sheet = await browser.newPage({ viewport: { width: Math.round(total * zoom), height: 900 } });
  await sheet.setContent(`<body style="margin:0;background:#888;zoom:${zoom};display:flex;gap:30px">${images.map((src) => `<img style="display:block;width:${width}px" src="${src}">`).join("")}</body>`);
  await sheet.screenshot({ path: `${values.out}/app-settings-${width}.jpg`, fullPage: true, type: "jpeg", quality: 62 });
  await sheet.close();
  console.log(`app-settings ${width}`);
}
await browser.close();
process.exit(failures ? 1 : 0);
