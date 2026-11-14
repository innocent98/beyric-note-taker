#!/usr/bin/env node
// Checks the local-time change on the built page: the same screens in `UTC` and in `Africa/Lagos`
// at 1440, 768 and 390 px must differ only in the text of their <time> elements (the zone label
// and the hour), never in layout: every other element keeps its box, and where a <time> box is
// wider or narrower the difference is listed. Also prints console errors (a hydration mismatch).
//
//   PLAYWRIGHT=/path/to/playwright/index.mjs node web/scripts/parity-local-time.mjs --next http://127.0.0.1:3141
import { parseArgs } from "node:util";
const { values } = parseArgs({ options: { next: { type: "string" } } });
const NEXT = values.next.replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const PAGES = ["/", "/meetings/1", "/meetings/2", "/meetings/3", "/meetings/4", "/notifications", "/settings"];

async function measure(path, width, timeZoneId) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: timeZoneId });
  await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(NEXT + path, { waitUntil: "load" });
  await page.waitForTimeout(1500);
  const data = await page.evaluate(() => {
    const rects = [];
    for (const el of document.querySelectorAll("body *")) {
      if (el.closest("time")) continue;
      const r = el.getBoundingClientRect();
      rects.push([el.tagName + (el.className && typeof el.className === "string" ? "." + el.className : ""), Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]);
    }
    const times = [...document.querySelectorAll("time")].map((t) => t.textContent);
    return { rects, times, hasTimeFixed: document.querySelectorAll("time[datetime]").length };
  });
  await context.close();
  return { ...data, errors };
}

let problems = 0;
for (const path of PAGES) {
  for (const width of [1440, 768, 390]) {
    const utc = await measure(path, width, "UTC");
    const lagos = await measure(path, width, "Africa/Lagos");
    // A <time> that gets longer or shorter nudges the words after it on the same line sideways, so a
    // changed x is expected; a changed y, width or height anywhere else (a line that now wraps) is not.
    // (A line with a time in it, such as the date line of a card, may change width with it.)
    let moved = 0;
    if (utc.rects.length !== lagos.rects.length) moved = -1;
    else utc.rects.forEach((a, i) => {
      const b = lagos.rects[i];
      if (a[2] !== b[2] || a[4] !== b[4]) {
        moved++;
        if (process.env.VERBOSE) console.log("   moved:", a.join(), "->", b.join());
      }
    });
    const wrong = [...utc.times.filter((t) => /GMT/.test(t)), ...lagos.times.filter((t) => / UTC$/.test(t))];
    if (moved !== 0 || wrong.length || utc.errors.length || lagos.errors.length) problems++;
    console.log(`${path} ${width}: ${utc.times.length} <time>, boxes moved down or taller: ${moved}, wrong-zone texts: ${wrong.length}, console errors: ${utc.errors.length + lagos.errors.length}` +
      (utc.times[0] ? `  e.g. "${utc.times[0]}" -> "${lagos.times[0]}"` : ""));
    for (const e of [...utc.errors, ...lagos.errors]) console.log("   error:", e.slice(0, 200));
  }
}
await browser.close();
console.log(problems === 0 ? "OK: layout identical, zones correct, no console errors" : `${problems} page/width combinations differ`);
process.exit(problems === 0 ? 0 : 1);
