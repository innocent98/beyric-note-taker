#!/usr/bin/env node
// Parity check for the Outlook parts of Settings > Calendar against the designer's file
// (docs/ui/Beyric Note Taker redesign/Beyric Note Taker.dc.html: `connectOutlook`, `outlookLabel`,
// the two buttons and the connected row). Two states:
//   A  not connected, Outlook on: the two buttons side by side ("Connect Google Calendar",
//      "Connect Microsoft Outlook")
//   B  Outlook connected: the row with the provider name, "address . Connected" and Disconnect
// For each pair of the same part it prints the computed styles on both sides and exits 1 when any
// differ, then writes one side-by-side JPEG per state and width (design on the left).
//
//   python3 -m http.server 8791 --directory "docs/ui/Beyric Note Taker redesign"
//   node scripts/mock-api.mjs --port 8031 --calendar microsoft_on        (state A; restart with microsoft_connected for B)
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run build && PORT=3100 node .next/standalone/server.js
//   PLAYWRIGHT=/path/to/playwright-core/index.mjs CHROME=/path/to/chrome \
//     node scripts/parity-outlook.mjs --state A|B --design http://127.0.0.1:8791 --next http://127.0.0.1:3100 --out docs/ui/parity/after
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({
  options: { state: { type: "string" }, design: { type: "string" }, next: { type: "string" }, out: { type: "string" } },
});
const STATE = values.state;
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const WIDTHS = [1440, 768, 390];
const PROPS = [
  "fontSize", "fontWeight", "lineHeight", "color", "backgroundColor", "paddingTop", "paddingRight", "paddingBottom",
  "paddingLeft", "borderTopWidth", "borderTopColor", "borderTopStyle", "borderRadius", "letterSpacing", "display", "gap",
];

mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

async function open(url, width, signedIn, designSteps = false) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  if (designSteps) {
    await page.getByText("Settings", { exact: true }).first().evaluate((el) => el.click());
    await page.waitForTimeout(400);
    if (STATE === "B") {
      await page.getByText("Connect Microsoft Outlook", { exact: true }).first().evaluate((el) => el.click());
      await page.waitForTimeout(1500);
    }
  }
  return { context, page };
}

// Computed styles of the element, plus its box relative to the Calendar card (the first section).
const measure = (page, finder) =>
  page.evaluate(
    ([source, props]) => {
      const el = new Function("return (" + source + ")")()();
      if (!el) return null;
      const cs = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const section = document.querySelector("section[aria-labelledby='set-calendar']") ?? document.querySelector("section");
      const sr = section.getBoundingClientRect();
      const out = Object.fromEntries(props.map((p) => [p, cs[p]]));
      out.x = Math.round(rect.left - sr.left);
      out.y = Math.round(rect.top - sr.top);
      out.w = Math.round(rect.width);
      out.h = Math.round(rect.height);
      return out;
    },
    [finder.toString(), PROPS],
  );

// Finders run in the page. A button by its visible words; a leaf element by its own text.
const button = (text) => `() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === ${JSON.stringify(text)})`;
const leafExpr = (text) =>
  `[...document.querySelectorAll("div,span")].find((e) => e.textContent.trim() === ${JSON.stringify(text)} && [...e.children].every((c) => c.tagName === "svg" || c.classList.contains("sc-interp") || c.children.length === 0))`;
const leaf = (text) => `() => ${leafExpr(text)}`;

const pairs =
  STATE === "A"
    ? {
        "Connect Google Calendar button": [button("Connect Google Calendar"), button("Connect Google Calendar")],
        "Connect Microsoft Outlook button": [button("Connect Microsoft Outlook"), button("Connect Microsoft Outlook")],
      }
    : {
        "connected row (box)": [
          `() => (${leafExpr("Microsoft Outlook")}).parentElement.parentElement.parentElement`,
          `() => document.querySelector(".cal-account")`,
        ],
        "provider name": [leaf("Microsoft Outlook"), `() => document.querySelector(".cal-provider")`],
        "address and state": [
          `() => (${leafExpr("Microsoft Outlook")}).nextElementSibling`,
          `() => document.querySelector(".cal-account-sub")`,
        ],
        "Disconnect button": [button("Disconnect"), button("Disconnect")],
      };

let failures = 0;
const design = await open(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, 1440, false, true);
const next = await open(`${NEXT}/settings`, 1440, true);
for (const [name, [d, n]] of Object.entries(pairs)) {
  const [a, b] = [await measure(design.page, d), await measure(next.page, n)];
  if (!a || !b) {
    console.log(`MISSING  ${name}: design=${!!a} next=${!!b}`);
    failures++;
    continue;
  }
  // The text differs by design (their sample address), so the width of a text block and the row
  // fit are not compared; the type, colour, box model and position are.
  const skip = (k) => (["w"].includes(k) && /address|provider|row/.test(name)) || (k === "h" && /address/.test(name));
  const diffs = Object.keys(a).filter((k) => a[k] !== b[k] && !skip(k));
  if (!diffs.length) console.log(`same     ${name}`);
  else {
    failures++;
    console.log(`DIFFERS  ${name}`);
    for (const k of diffs) console.log(`           ${k}: design ${a[k]} | next ${b[k]}`);
  }
}
if (STATE === "A") {
  // Side by side: both buttons share one row in the designer's file and in ours.
  const y = async (page, text) => (await measure(page, button(text))).y;
  const same = (await y(design.page, "Connect Google Calendar")) === (await y(design.page, "Connect Microsoft Outlook"));
  const sameNext = (await y(next.page, "Connect Google Calendar")) === (await y(next.page, "Connect Microsoft Outlook"));
  console.log(same === sameNext ? "same     buttons share one row" : "DIFFERS  buttons share one row");
  if (same !== sameNext) failures++;
}
await design.context.close();
await next.context.close();

for (const width of WIDTHS) {
  const shoot = async (url, signedIn, steps) => {
    const { context, page } = await open(url, width, signedIn, steps);
    const section = (await page.locator("section[aria-labelledby='set-calendar']").count()) ? page.locator("section[aria-labelledby='set-calendar']") : page.locator("section").first();
    const png = await section.screenshot();
    await context.close();
    return `data:image/png;base64,${png.toString("base64")}`;
  };
  const images = [await shoot(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, false, true), await shoot(`${NEXT}/settings`, true, false)];
  const total = 2 * width + 30;
  const zoom = Math.min(1, 1500 / total);
  const sheet = await browser.newPage({ viewport: { width: Math.round(total * zoom), height: 600 } });
  await sheet.setContent(`<body style="margin:0;background:#888;zoom:${zoom};display:flex;gap:30px;align-items:flex-start">${images.map((src) => `<img style="display:block;max-width:${width}px" src="${src}">`).join("")}</body>`);
  await sheet.screenshot({ path: `${values.out}/outlook-settings-calendar-${STATE.toLowerCase()}-${width}.jpg`, fullPage: true, type: "jpeg", quality: 70 });
  await sheet.close();
  console.log(`outlook-settings-calendar-${STATE.toLowerCase()} ${width}`);
}
await browser.close();
process.exit(failures ? 1 : 0);
