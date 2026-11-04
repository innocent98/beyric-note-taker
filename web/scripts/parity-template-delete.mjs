#!/usr/bin/env node
// Measures the "Delete template" button, the Save button and the foot row they sit in, on the
// designer's prototype and on the built Templates page at 1440 px, and prints both side by side:
// positions inside the editor card (so the different content above does not matter), sizes and
// computed styles absolute.
//
//   (same servers as parity-shots.mjs)
//   PLAYWRIGHT=/path/to/playwright/index.mjs [CHROME=...] node scripts/parity-template-delete.mjs \
//     --design http://127.0.0.1:8700 --next http://127.0.0.1:3100
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" } } });
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

const STYLES = [
  "fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "paddingTop", "paddingRight",
  "paddingBottom", "paddingLeft", "cursor", "justifyContent", "alignItems", "display", "columnGap",
];

// Runs in the page: what to measure, found by text, inside the card that holds the Name field.
function measure(STYLES) {
  const card = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.borderTopWidth === "1px" && cs.borderTopLeftRadius === "12px" && e.querySelector("textarea")) return e;
    }
    return null;
  };
  const byText = (text) => Array.from(document.querySelectorAll("button")).find((el) => el.textContent.trim() === text);
  const del = byText("Delete template");
  const save = byText("Save");
  const box = card(del).getBoundingClientRect();
  let foot = del.parentElement;
  while (!foot.contains(save)) foot = foot.parentElement;
  const one = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const style = {};
    for (const key of STYLES) style[key] = cs[key];
    return { x: Math.round(r.left - box.left), y: Math.round(r.top - box.top), w: Math.round(r.width), h: Math.round(r.height), style };
  };
  return { delete: one(del), save: one(save), foot: one(foot) };
}

async function run(url, clicks, signedIn, base) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: base }]);
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(2500);
  for (const text of clicks) {
    await page.getByText(text, { exact: true }).first().evaluate((el) => el.click());
    await page.waitForTimeout(400);
  }
  const result = await page.evaluate(measure, STYLES).catch((error) => {
    throw new Error(`${url}: ${error.message}`);
  });
  await context.close();
  return result;
}

const design = await run(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, ["Templates"], false, DESIGN);
const next = await run(`${NEXT}/templates`, [], true, NEXT);

let different = 0;
const rows = ["| Element | Design | Built | Result |", "|---|---|---|---|"];
for (const key of ["delete", "save", "foot"]) {
  const { style: designStyle, ...designBox } = design[key];
  const { style: nextStyle, ...nextBox } = next[key];
  for (const [label, a, b] of [
    [`${key}: box (x, y, w, h in the card)`, designBox, nextBox],
    [`${key}: styles`, designStyle, nextStyle],
  ]) {
    const same = JSON.stringify(a) === JSON.stringify(b);
    if (!same) different += 1;
    rows.push(`| ${label} | \`${JSON.stringify(a)}\` | \`${JSON.stringify(b)}\` | ${same ? "identical" : "DIFFERENT"} |`);
  }
}
console.log(rows.join("\n"));
console.log(`\n${different} differences`);
await browser.close();
