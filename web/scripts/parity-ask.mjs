#!/usr/bin/env node
// Evidence for the Ask Beyric screens (docs/sop/2026-11-08-ask-beyric-screens.md): the computed styles and size of
// the same element in the designer's prototype and in the Next page at 1440 px, after asking "What did we decide
// about pricing?" on each, plus the transcript row highlight colour. Exit 1 when a drawn element differs.
//
//   python3 -m http.server 8700 --directory "docs/ui/Beyric Note Taker redesign"
//   node scripts/mock-api.mjs --port 8031 ; (build and serve Next as in parity-shots.mjs, FASTAPI_INTERNAL_URL set at both steps)
//   PLAYWRIGHT=/path/to/playwright/index.mjs node scripts/parity-ask.mjs \
//     --design http://127.0.0.1:8700 --next http://127.0.0.1:3100 --out docs/ui/parity/after/ask-styles.json
import { parseArgs } from "node:util";
import { writeFileSync } from "node:fs";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" }, out: { type: "string" } } });
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const APP = `${DESIGN}/Beyric%20Note%20Taker.dc.html`;
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

const KEYS = ["width", "height", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "color", "backgroundColor", "borderRadius", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "borderTopWidth", "borderTopStyle", "borderTopColor", "gap", "boxShadow"];
const PICK = `window.__pick = (el) => { const cs = getComputedStyle(el); return Object.fromEntries(${JSON.stringify(KEYS)}.map((k) => [k, cs[k]])); }; window.__box = (el) => (el.classList.contains("sc-interp") ? el.parentElement : el);`;
const PH = "e.g. What did we decide about pricing?";

async function open(url, side) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: "Africa/Lagos" });
  if (side === "next") await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  await page.addInitScript(PICK);
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1000);
  if (side === "design") await page.getByText("Ask Beyric", { exact: true }).first().evaluate((el) => el.click());
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: "What did we decide about pricing?" }).first().evaluate((el) => el.click());
  await page.waitForTimeout(2000);
  return page;
}
const pick = (locator, up = 0) =>
  locator.first().evaluate((el, n) => { let e = window.__box(el); for (let i = 0; i < n; i++) e = e.parentElement; return window.__pick(e); }, up);

const rows = [];
// Sizes within 1 px are the same (the two pages measure the same text a fraction of a pixel apart); `ignore` names
// the fields that follow the words, which differ on purpose (the design's sample sentences and its times).
const px = (v) => (/^-?[\d.]+px$/.test(v) ? parseFloat(v) : null);
const alike = (a, b) => a === b || (px(a) !== null && px(b) !== null && Math.abs(px(a) - px(b)) <= 1);
function compare(what, design, next, ignore = []) {
  const diff = Object.keys(design).filter((k) => !ignore.includes(k) && !alike(design[k], next[k])).map((k) => `${k}: ${design[k]} | ${next[k]}`);
  rows.push({ what, same: diff.length === 0, diff, design, next });
}

const d = await open(APP, "design");
const n = await open(`${NEXT}/ask`, "next");
const both = async (what, fd, fn = fd, up = 0, ignore = []) => compare(what, await pick(fd(d), up), await pick(fn(n), up), ignore);
await both("Page title", (p) => p.getByRole("heading", { name: "Ask Beyric" }));
await both("Subtitle", (p) => p.getByText("Ask about anything said, decided or assigned in your meetings. Every answer links to the moment it came from.", { exact: true }));
await both("Input box (the white bar)", (p) => p.getByPlaceholder(PH), undefined, 1);
await both("Input", (p) => p.getByPlaceholder(PH));
await both("Ask button", (p) => p.getByRole("button", { name: "Ask", exact: true }));
await both("Suggestion chip", (p) => p.getByRole("button", { name: "When is the launch?" }));
await both("Question row (the card's head)", (p) => p.locator("section").getByText("AT", { exact: true }), undefined, 1);
await both("Initials circle", (p) => p.locator("section").getByText("AT", { exact: true }));
await both("Card (height follows the words and our caveat line)", (p) => p.locator("section").getByText("AT", { exact: true }), undefined, 2, ["height"]);
await both("Bullet point text", (p) => p.getByText("Customer interviews will happen once onboarding is stable.", { exact: true }));
await both("Source chip (width follows the label: a day, not a time)", (p) => p.locator("section").getByText(/^Beyric Monday planning/), undefined, 0, ["width"]);
// The design's lead and the scope line carry its own sample words, so they are found by place.
compare("Lead sentence (height follows the words)", await d.locator("section p").first().evaluate((el) => window.__pick(el)), await n.locator(".ask-lead").first().evaluate((el) => window.__pick(el)), ["height"]);
compare("Scope line", await d.getByText(/^Searches \d+ meetings/).first().evaluate((el) => window.__pick(window.__box(el))), await n.getByText(/^Searches \d+ meetings/).first().evaluate((el) => window.__pick(el)));

// The transcript row highlight: the colour the design gives the cited row and the one the page gives row N.
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.context().addCookies([{ name: "session", value: "owner", url: NEXT }]);
await page.goto(`${NEXT}/meetings/8?tab=transcript&line=12`, { waitUntil: "load" });
await page.waitForTimeout(1500);
const ours = await page.locator("#tl-12").evaluate((el) => ({ bg: getComputedStyle(el).backgroundColor, radius: getComputedStyle(el).borderRadius, padding: getComputedStyle(el).padding, margin: getComputedStyle(el).margin }));
const plain = await page.locator("#tl-11").evaluate((el) => getComputedStyle(el).backgroundColor);
rows.push({ what: "Transcript row highlight", same: ours.bg === "rgb(255, 244, 214)" && ours.radius === "8px" && ours.padding === "12px 10px" && ours.margin === "0px -10px", diff: [], design: { backgroundColor: "rgb(255, 244, 214)", borderRadius: "8px", padding: "12px 10px", margin: "0px -10px" }, next: { ...ours, otherRows: plain } });

await browser.close();
for (const r of rows) console.log(`${r.same ? "same " : "DIFF "} ${r.what}${r.same ? "" : "\n      " + r.diff.join("\n      ")}`);
if (values.out) writeFileSync(values.out, JSON.stringify(rows, null, 2) + "\n");
process.exit(rows.every((r) => r.same) ? 0 : 1);
