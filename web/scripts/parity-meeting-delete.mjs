#!/usr/bin/env node
// Evidence for the lead-designed meeting delete (the designer's file has none): shots of the meeting
// page with the red text button and with the inline ask open, at 1440, 768 and 390 px, the list with
// its "Meeting deleted" toast, and the computed styles of "Delete meeting" and its ask measured
// against the designer's "Delete template" button and "Remove from log" ask (same tokens, nothing new).
//
//   python3 -m http.server 8701 --directory "docs/ui/Beyric Note Taker redesign"
//   node scripts/mock-api.mjs --port 8032 ; (build + serve Next on 3101 as in parity-shots.mjs)
//   PLAYWRIGHT=/path/to/playwright/index.mjs node scripts/parity-meeting-delete.mjs \
//     --design http://127.0.0.1:8701 --next http://127.0.0.1:3101 --mock http://127.0.0.1:8032 --out docs/ui/parity/meeting-delete
import { parseArgs } from "node:util";
import { mkdirSync } from "node:fs";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" }, mock: { type: "string" }, out: { type: "string" } } });
const NEXT = values.next.replace(/\/$/, "");
const DESIGN = values.design.replace(/\/$/, "");
mkdirSync(values.out, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const STYLES = ["fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "borderTopColor", "borderRadius", "paddingTop", "paddingLeft", "cursor"];
const pick = (el) => { const cs = getComputedStyle(el); return Object.fromEntries(["fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "borderTopColor", "borderRadius", "paddingTop", "paddingLeft", "cursor"].map((k) => [k, cs[k]])); };

async function ctx(width, base) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: "Africa/Lagos" });
  await context.addCookies([{ name: "session", value: "owner", url: base }]);
  return context;
}

for (const width of [1440, 768, 390]) {
  await fetch(`${values.mock}/__mock/reset`).catch(() => {});
  const context = await ctx(width, NEXT);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`${NEXT}/meetings/8`, { waitUntil: "load" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${values.out}/meeting-${width}.jpg`, fullPage: true, type: "jpeg", quality: 82 });
  await page.getByRole("button", { name: "Delete meeting" }).click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${values.out}/meeting-ask-${width}.jpg`, fullPage: true, type: "jpeg", quality: 82 });
  if (width === 1440) {
    const button = await page.evaluate(() => {
      const ask = document.querySelector(".meeting-delete-ask");
      const del = ask.querySelector("button.btn-danger");
      const cs = (el) => { const c = getComputedStyle(el); return Object.fromEntries(["fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "borderTopColor", "borderRadius", "paddingTop", "paddingLeft", "cursor", "height"].map((k) => [k, c[k]])); };
      return { ask: cs(ask), askText: cs(ask.querySelector("p")), confirmButton: cs(del) };
    });
    console.log("built ask + confirm:", JSON.stringify(button));
    await page.getByRole("button", { name: "Cancel" }).click();
    console.log("built red text button:", JSON.stringify(await page.evaluate(() => { const c = getComputedStyle(document.querySelector(".meeting-delete")); return Object.fromEntries(["fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "paddingTop", "paddingLeft", "cursor"].map((k) => [k, c[k]])); })));
  }
  if (width === 1440) await page.getByRole("button", { name: "Delete meeting" }).click();
  await page.locator(".meeting-delete-ask").getByRole("button", { name: "Delete meeting" }).click();
  await page.waitForURL(`${NEXT}/`, { timeout: 8000 });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${values.out}/list-toast-${width}.jpg`, fullPage: false, type: "jpeg", quality: 82 });
  console.log(width, "toast:", await page.locator(".toast").first().textContent().catch(() => null), "errors:", errors);
  await context.close();
}

// The designer's own: "Delete template" (red text button) and the Decisions "Remove from log" ask.
const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const dp = await dctx.newPage();
await dp.goto(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, { waitUntil: "load" });
await dp.waitForTimeout(2500);
const click = async (text) => { await dp.getByText(text, { exact: true }).first().evaluate((el) => el.click()); await dp.waitForTimeout(400); };
await click("Templates");
console.log("design Delete template:", JSON.stringify(await dp.evaluate((pickSrc) => { const b = [...document.querySelectorAll("button")].find((e) => e.textContent.trim() === "Delete template"); const c = getComputedStyle(b); return Object.fromEntries(["fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "paddingTop", "paddingLeft", "cursor"].map((k) => [k, c[k]])); })));
await click("Decisions");
await dp.getByText("Remove from log", { exact: true }).first().evaluate((el) => el.click());
await dp.waitForTimeout(400);
console.log("design Remove ask:", JSON.stringify(await dp.evaluate(() => { const b = [...document.querySelectorAll("button")].find((e) => e.textContent.trim() === "Remove"); const box = b.parentElement.parentElement.parentElement; const bc = getComputedStyle(b), c = getComputedStyle(box); const f = (s, ks) => Object.fromEntries(ks.map((k) => [k, s[k]])); return { ask: f(c, ["backgroundColor", "borderTopWidth", "borderTopColor", "borderRadius", "paddingTop", "paddingLeft"]), confirmButton: f(bc, ["fontSize", "fontWeight", "color", "backgroundColor", "borderRadius", "height"]) }; })));
await dp.screenshot({ path: `${values.out}/design-remove-ask-1440.jpg`, type: "jpeg", quality: 82 });
await browser.close();
