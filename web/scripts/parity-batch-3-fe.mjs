#!/usr/bin/env node
// Evidence for UI parity batch 3, front end only (docs/sop/2026-11-07-ui-parity-batch-3-fe.md): the computed
// styles of the same element in the designer's prototype and in the Next page, side by side, for
//   B10 the Series score tiles, B11 the red overdue label and the overdue group heading,
//   B34 the "Remove from log" button inside an open decision and the "No decisions match." line.
//
//   python3 -m http.server 8710 --directory "docs/ui/Beyric Note Taker redesign"
//   node scripts/mock-api.mjs --port 8041 --overdue ; (build and serve Next as in parity-shots.mjs)
//   PLAYWRIGHT=/path/to/playwright/index.mjs node scripts/parity-batch-3-fe.mjs \
//     --design http://127.0.0.1:8710 --next http://127.0.0.1:3110 --out docs/ui/parity/after/batch-3-fe-styles.json
import { parseArgs } from "node:util";
import { writeFileSync } from "node:fs";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" }, out: { type: "string" } } });
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const APP = `${DESIGN}/Beyric%20Note%20Taker.dc.html`;
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

const KEYS = ["fontSize", "fontWeight", "letterSpacing", "color", "backgroundColor", "borderRadius", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "borderTopWidth", "borderTopStyle", "borderTopColor", "textAlign", "cursor"];
// Runs in the page (added to every page before it loads), so the evaluate callbacks below can call it.
const PICK_SOURCE = `window.__pick = (el) => { const cs = getComputedStyle(el); return Object.fromEntries(${JSON.stringify(KEYS)}.map((k) => [k, cs[k]])); }; window.__box = (el) => (el.classList.contains("sc-interp") ? el.parentElement : el);`;
// The prototype wraps every interpolated text in <span class="sc-interp">; the element that is compared is its parent.

async function open(url, signedIn) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: "Africa/Lagos" });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  if (signedIn) await context.clock.setFixedTime(new Date("2026-11-02T10:00:00Z"));
  const page = await context.newPage();
  await page.addInitScript(PICK_SOURCE);
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
  return page;
}
const click = (page, text, which = "first") =>
  page.getByText(text, { exact: true })[which]().evaluate((el) => el.click()).then(() => page.waitForTimeout(400));
const style = (page, text, which = "first") => page.getByText(text, { exact: true })[which]().evaluate((el) => window.__pick(window.__box(el)));
const parentStyle = (page, text) => page.getByText(text, { exact: true }).first().evaluate((el) => window.__pick(window.__box(el).parentElement));

const rows = [];
function compare(what, design, next) {
  const diff = Object.keys(design).filter((k) => design[k] !== next[k]).map((k) => `${k}: ${design[k]} | ${next[k]}`);
  rows.push({ what, same: diff.length === 0, diff, design, next });
}

// B10: the tiles. The design's number sits in the tile, the label under it; the tile is the parent of both.
{
  const d = await open(APP, false);
  await click(d, "Next: Mon 9 Nov, 14:00");
  const n = await open(`${NEXT}/series/1`, true);
  compare("B10 tile (box)", await parentStyle(d, "Done since last meeting"), await parentStyle(n, "Done since last meeting"));
  compare("B10 label", await style(d, "Done since last meeting"), await style(n, "Done since last meeting"));
  compare("B10 label (Still open)", await style(d, "Still open"), await style(n, "Still open"));
  // Numbers: the design's Overdue is 0 (plain ink); ours is 2 on the mock's overdue data (red). The ink one is
  // compared on "Still open"'s number, the teal one on "Done", the red one is checked against the token below.
  const num = (page, label) => page.getByText(label, { exact: true }).first().evaluate((el) => window.__pick(window.__box(el).previousElementSibling));
  compare("B10 Done number (teal)", await num(d, "Done since last meeting"), await num(n, "Done since last meeting"));
  compare("B10 Still open number (ink)", await num(d, "Still open"), await num(n, "Still open"));
  const late = await num(n, "Overdue");
  rows.push({ what: "B10 Overdue number when above 0 (red #B42318)", same: late.color === "rgb(180, 35, 24)", diff: [], next: late });
  const lateInk = await num(d, "Overdue");
  rows.push({ what: "B10 design's Overdue number at 0 is ink (the design draws it, ours draws 0 as ink too: see the unit test)", same: lateInk.color === "rgb(20, 25, 23)", diff: [], design: lateInk });
}

// B11: the card label and the group heading (Tasks, Group by Due date).
{
  const d = await open(APP, false);
  await click(d, "Tasks");
  await click(d, "Due date");
  const n = await open(`${NEXT}/tasks`, true);
  await click(n, "Due date");
  compare("B11 card label (red)", await style(d, "Overdue, Fri 30 Oct"), await style(n, "Overdue, Fri 30 Oct"));
  compare("B11 group heading", await style(d, "Overdue, was due Fri 30 Oct"), await style(n, "Overdue, was due Fri 30 Oct"));
  rows.push({ what: "B11 words (present on both sides)", same: true, diff: [] });
}

// B34: Remove from log in the open row, and the empty search line.
{
  const d = await open(APP, false);
  await click(d, "Decisions");
  await d.waitForTimeout(1200);
  await click(d, "Beyric Note Taker is intended for internal use.");
  const n = await open(`${NEXT}/decisions`, true);
  await click(n, "Keep the data export in CSV for version one; Excel can wait.");
  const dBtn = d.getByRole("button", { name: "Remove from log", exact: true }).first();
  const nBtn = n.getByRole("button", { name: "Remove from log", exact: true }).first();
  compare("B34 Remove from log (resting)", await dBtn.evaluate((el) => window.__pick(el)), await nBtn.evaluate((el) => window.__pick(el)));
  await dBtn.hover();
  await nBtn.hover();
  await d.waitForTimeout(300);
  compare("B34 Remove from log (hover: red)", await dBtn.evaluate((el) => window.__pick(el)), await nBtn.evaluate((el) => window.__pick(el)));
  const panel = (page) => page.getByRole("button", { name: "Remove from log", exact: true }).first().evaluate((el) => window.__pick(el.parentElement));
  compare("B34 expanded panel box", await panel(d), await panel(n));
  // The empty search line.
  await d.getByPlaceholder("Search decisions").fill("zebra");
  await n.getByPlaceholder("Search decisions").fill("zebra");
  await d.waitForTimeout(800);
  await n.waitForTimeout(1500);
  compare("B34 No decisions match.", await style(d, "No decisions match."), await style(n, "No decisions match."));
}

writeFileSync(values.out, JSON.stringify(rows, null, 2) + "\n");
for (const r of rows) console.log(`${r.same ? "SAME" : "DIFF"}  ${r.what}${r.diff.length ? "\n      " + r.diff.join("\n      ") : ""}`);
await browser.close();
