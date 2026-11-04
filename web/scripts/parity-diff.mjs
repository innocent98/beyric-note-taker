#!/usr/bin/env node
// Diffs positions and computed styles of the same labels and boxes on the designer's prototype and
// on the built page, at 1440 px: the Settings sections below Calendar and the Recurring cards.
// Positions are measured inside the section's own box (so the one added line per section does not
// shift every later number), sizes are absolute. Prints every difference, then a count.
//
//   (same servers as parity-shots.mjs, the mock started with --calendar connected)
//   PLAYWRIGHT=/path/to/playwright/index.mjs [CHROME=...] node scripts/parity-diff.mjs \
//     --design http://127.0.0.1:8730 --next http://127.0.0.1:3731 [--out file.txt]
import { parseArgs } from "node:util";
import { writeFileSync } from "node:fs";

const { values } = parseArgs({ options: { design: { type: "string" }, next: { type: "string" }, out: { type: "string" } } });
const DESIGN = values.design.replace(/\/$/, "");
const NEXT = values.next.replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch({ executablePath: process.env.CHROME });

const STYLES = [
  "fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "borderTopColor", "borderRadius",
  "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "opacity", "letterSpacing", "textTransform",
];

// Runs in the page. `spec` lists what to measure in each section.
function measureInPage({ sections, STYLES }) {
  const out = {};
  const all = Array.from(document.querySelectorAll("body *"));
  const boxOf = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.borderTopWidth === "1px" && cs.borderTopLeftRadius === "12px") return e;
    }
    return null;
  };
  // The first element in a card whose own text is exactly `text` (the sidebar's "Tasks" is in no card).
  const textEl = (text) => {
    const same = all.filter((el) => el.textContent.trim() === text && boxOf(el));
    return same.find((el) => !same.some((other) => other !== el && el.contains(other))) ?? null;
  };
  for (const section of sections) {
    const title = textEl(section.title);
    const box = title ? boxOf(title) : null;
    if (!box) {
      out[section.title] = { missing: true };
      continue;
    }
    const b = box.getBoundingClientRect();
    const rec = (el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const style = {};
      for (const s of STYLES) style[s] = cs[s];
      return { x: Math.round(r.x - b.x), y: Math.round(r.y - b.y), w: Math.round(r.width), h: Math.round(r.height), style };
    };
    const entries = { "[box]": { x: 0, y: 0, w: Math.round(b.width), h: Math.round(b.height), style: rec(box).style } };
    for (const text of section.texts) {
      const el = textEl(text);
      entries[text] = el ? rec(el) : { missing: true };
    }
    // Controls inside the box, in document order.
    const within = (sel) => Array.from(box.querySelectorAll(sel));
    within("select").forEach((el, i) => (entries[`[select ${i + 1}]`] = rec(el)));
    within("button").filter((el) => !el.getAttribute("role")).forEach((el, i) => (entries[`[button ${i + 1}: ${el.textContent.trim()}]`] = rec(el)));
    // A switch is a 36x20 pill (a div in the design, a button here).
    within("*")
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return Math.round(r.width) === 36 && Math.round(r.height) === 20 && getComputedStyle(el).borderTopLeftRadius !== "0px";
      })
      .forEach((el, i) => (entries[`[switch ${i + 1}]`] = rec(el)));
    out[section.title] = entries;
  }
  return out;
}

const SETTINGS = [
  {
    title: "Sprints and reminders",
    texts: [
      "Sprints and reminders", "Workspace defaults. Projects can use their own sprint length.", "Owners and admins",
      "Sprint length", "Open and close sprints at recurring meetings", "Remind attendees before recurring meetings",
      "The email covers what the last meeting focused on, decisions made, open questions and unfinished tasks.",
    ],
  },
  {
    title: "Tasks",
    texts: [
      "Tasks", "What happens to action items after a meeting ends.", "Create tasks from action items",
      "Each action item becomes a task assigned to the teammate named as its owner.", "Email assignees",
      "Send each person a list of their new tasks after the meeting.", "Sync with your project tool",
      "Send tasks, projects and sprints to a tool your team already uses.", "Post tasks to Slack",
      "Share new tasks in a channel so the team can track them there.",
    ],
  },
  {
    title: "Default notes language",
    texts: [
      "Default notes language",
      "Notes and transcripts open in this language. You can switch back to the original on any meeting.",
    ],
  },
];
const RECURRING = [
  { title: "Beyric Monday planning", texts: ["Beyric Monday planning", "Weekly on Mondays, 14:00", "Next: Mon 9 Nov, 14:00"] },
  { title: "Beyric Friday review", texts: ["Beyric Friday review", "Weekly on Fridays, 15:00", "Next: Fri 6 Nov, 15:00"] },
];

async function open(url, signedIn, clicks) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
  for (const [text, wait] of clicks) {
    await page.getByText(text, { exact: true }).first().evaluate((el) => el.click());
    await page.waitForTimeout(wait ?? 400);
  }
  return { context, page };
}

const lines = [];
let differences = 0;
let compared = 0;
function compare(screen, label, a, b) {
  const entry = `${screen} / ${label}`;
  if (a.missing || b.missing) {
    differences++;
    lines.push(`MISSING ${entry}: design ${a.missing ? "absent" : "present"}, page ${b.missing ? "absent" : "present"}`);
    return;
  }
  compared++;
  const notes = [];
  for (const k of ["x", "w", "h"]) if (Math.abs(a[k] - b[k]) > 1) notes.push(`${k} ${a[k]} -> ${b[k]}`);
  if (Math.abs(a.y - b.y) > 1) notes.push(`y ${a.y} -> ${b.y}`);
  for (const s of STYLES) if (a.style[s] !== b.style[s]) notes.push(`${s} ${a.style[s]} -> ${b.style[s]}`);
  if (notes.length) {
    differences++;
    lines.push(`DIFF ${entry}: ${notes.join("; ")}`);
  }
}

const D = await open(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, false, [["Settings", 400], ["Connect Google Calendar", 1500]]);
const N = await open(`${NEXT}/settings`, true, []);
const dSet = await D.page.evaluate(measureInPage, { sections: SETTINGS, STYLES });
const nSet = await N.page.evaluate(measureInPage, { sections: SETTINGS, STYLES });
for (const s of SETTINGS) {
  const labels = new Set([...Object.keys(dSet[s.title]), ...Object.keys(nSet[s.title])]);
  for (const label of labels) compare("Settings", `${s.title} / ${label}`, dSet[s.title][label] ?? { missing: true }, nSet[s.title][label] ?? { missing: true });
}
await D.context.close();
await N.context.close();

const D2 = await open(`${DESIGN}/Beyric%20Note%20Taker.dc.html`, false, []);
const N2 = await open(`${NEXT}/`, true, []);
const dRec = await D2.page.evaluate(measureInPage, { sections: RECURRING, STYLES });
const nRec = await N2.page.evaluate(measureInPage, { sections: RECURRING, STYLES });
for (const s of RECURRING) {
  const labels = new Set([...Object.keys(dRec[s.title]), ...Object.keys(nRec[s.title])]);
  for (const label of labels) compare("Recurring", `${s.title} / ${label}`, dRec[s.title][label] ?? { missing: true }, nRec[s.title][label] ?? { missing: true });
}
await D2.context.close();
await N2.context.close();
await browser.close();

lines.push(`\n${compared} boxes and labels compared, ${differences} with a difference (positions inside each section; y within 1 px counts as equal).`);
const text = lines.join("\n");
console.log(text);
if (values.out) writeFileSync(values.out, text + "\n");
