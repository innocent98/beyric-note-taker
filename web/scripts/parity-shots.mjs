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
//     [--mock http://127.0.0.1:8031] [--tz Africa/Lagos] [--now 2026-11-02T10:00:00Z]
//
// `--mock` is the mock API's own address: before each Next shot of a screen that changes state (the
// translation screens) it is asked to start again from nothing (`/__mock/reset`).
//
// `--tz` sets the browser's time zone (Playwright `timezoneId`) on every page, so a run in
// `Africa/Lagos` and one in `UTC` show what a viewer in each place sees. Console errors and page
// errors of the Next pages are printed (a hydration mismatch shows as one).
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
    tz: { type: "string" },
    now: { type: "string" },
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
  // The notifications list shows each notice's day and time (no prototype).
  "app-notifications": [null, [], "/notifications", true],
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
  // Decisions and Review (the designer's states: run the mock with --decisions/--review failed|empty|loading
  // and the design copy with its "Preview states" default changed, see the SOP). Live list, the
  // inline remove ask and its toast, the rewrite offer, the row errors, the checked rows.
  "app-decisions": [APP, [["Decisions", "first", 1200]], "/decisions", true],
  "app-decisions-confirm": [APP, [["Decisions", "first", 1200], ["Remove from log", "first"]], "/decisions", true, [{ text: "Remove from log" }]],
  "app-decisions-removed": [
    APP,
    [["Decisions", "first", 1200], ["Remove from log", "first"], ["Remove", "first", 300]],
    "/decisions",
    true,
    [{ text: "Remove from log" }, { text: "Remove", wait: 500 }],
  ],
  // Decisions/Review loading: the mock's `retry-loading` fails the page load, then answers slowly.
  "app-decisions-loading": [APP, [["Decisions", "first", 300]], "/decisions", true, [{ text: "Try again", wait: 600 }]],
  "app-review-loading": [APP, [["Review", "first", 300]], "/review", true, [{ text: "Try again", wait: 600 }]],
  "app-review": [APP, [["Review", "first", 1200]], "/review", true],
  "app-review-offer": [
    APP,
    [["Review", "first", 1200], { fill: "e.g. company name", value: "Metrika" }, ["Add to vocabulary", "first", 400]],
    "/review",
    true,
    [{ fill: "e.g. company name", value: "Metrika" }, { text: "Add to vocabulary", wait: 600 }],
  ],
  "app-review-empty-term": [APP, [["Review", "first", 1200], ["Add to vocabulary", "first", 400]], "/review", true, [{ text: "Add to vocabulary", wait: 400 }]],
  "app-review-dup": [
    APP,
    [["Review", "first", 1200], { fill: "e.g. company name", value: "Beyric" }, ["Add to vocabulary", "first", 400]],
    "/review",
    true,
    [{ fill: "e.g. company name", value: "Beyric" }, { text: "Add to vocabulary", wait: 600 }],
  ],
  "app-review-gone": [null, [], "/review", true, [{ fill: "e.g. company name", value: "Gone" }, { text: "Add to vocabulary", wait: 600 }]],
  "app-settings-timezone": [
    APP,
    [["Settings", "first"], { select: "(UTC+9) Tokyo", design: "Tokyo", wait: 300 }],
    "/settings",
    true,
    [{ select: "(UTC+9) Tokyo", design: "Tokyo", next: "Time zone", wait: 500 }],
  ],
  // Tasks (W2). The prototype's "today" is Mon 2 Nov 2026: run with --now 2026-11-02T10:00:00Z. The board
  // has the sprint card, selector and Board / Backlog tabs (W9); W4 added the project tabs and the milestone card.
  "app-tasks": [APP, [["Tasks", "first", 800]], "/tasks", true],
  // Sprints (W9): the Backlog view, the next sprint on the Board, Close sprint (the toast says how many moved),
  // the Settings length with Custom, and a project's own length.
  "app-tasks-backlog": [APP, [["Tasks", "first", 800], ["Backlog", "first", 400]], "/tasks", true, [{ text: "Backlog", wait: 500 }]],
  "app-tasks-sprint-next": [
    APP,
    [["Tasks", "first", 800], { select: "Sprint 13 (next)", design: "Sprint 12 (current)", wait: 400 }],
    "/tasks",
    true,
    [{ select: "Sprint 13 (next)", next: "Sprint", wait: 500 }],
  ],
  "app-tasks-close-sprint": [APP, [["Tasks", "first", 800], ["Close sprint", "first", 400]], "/tasks", true, [{ text: "Close sprint", wait: 800 }]],
  "app-settings-sprint-custom": [
    APP,
    [["Settings", "first"], { select: "Custom", design: "1 week, 5 working days", wait: 300 }],
    "/settings",
    true,
    [{ select: "Custom", next: "Sprint length", wait: 800 }],
  ],
  "app-projects-sprint-length": [
    APP,
    [["Projects", "first", 800], { select: "2 weeks", design: "Workspace default", wait: 300 }],
    "/projects",
    true,
    [{ select: "2 weeks", next: "Sprint length", wait: 800 }],
  ],
  "app-tasks-due": [APP, [["Tasks", "first", 800], ["Due date", "first"]], "/tasks", true, [{ text: "Due date" }]],
  "app-tasks-drawer": [
    APP,
    [["Tasks", "first", 800], ["Finish payment checkout", "first", 400]],
    "/tasks",
    true,
    [{ text: "Finish payment checkout", wait: 400 }],
  ],
  "app-tasks-review": [APP, [["Tasks", "first", 800], ["Review", "first", 400]], "/tasks", true, [{ text: "Review", wait: 400 }]],
  // Projects (W4). Recurring meetings is drawn off with a "Not available yet" line (W8); the Sprint length
  // select and the two sprint figures are live (W9); the project tab on the board and the Check project line are the designer's.
  "app-projects": [APP, [["Projects", "first", 800]], "/projects", true],
  "app-projects-second": [APP, [["Projects", "first", 800], ["Beyric Note Taker", "last", 400]], "/projects", true, [{ click: "Beyric Note Taker", wait: 400 }]],
  "app-tasks-project": [APP, [["Tasks", "first", 800], ["Beyric Note Taker", "last", 400]], "/tasks", true, [{ click: "Beyric Note Taker", wait: 400 }]],
  "app-tasks-check-project": [
    APP,
    [["Tasks", "first", 800], ["Support Daniel Okoro with testing during the week", "first", 400]],
    "/tasks",
    true,
    [{ text: "Support Daniel Okoro with testing during the week", wait: 400 }],
  ],
  // LEAD-DESIGNED, no prototype: the offer on a meeting page, "Set milestone: <name>, <date>?" with Confirm.
  "app-detail-milestone-offer": [null, [], "/meetings/8", true],
  // Team (W3). The designer drew the Team screen and the invite dialog; the screens with a null design
  // are LEAD-DESIGNED (no prototype): alias editing, the remove question, the Share with team switch on
  // the meeting page and the invitation link page. The mock's tokens: new, existing, onteam, dead.
  "app-team": [APP, [["Team", "first", 800]], "/team", true],
  "app-team-invite": [APP, [["Team", "first", 800], ["Invite people", "first", 400]], "/team", true, [{ text: "Invite people", wait: 500 }]],
  "app-team-aliases": [null, [], "/team", true, [{ text: "Edit names", wait: 400 }]],
  "app-team-remove": [null, [], "/team", true, [{ click: "Remove Amara Nwosu", wait: 400 }]],
  "app-tasks-invite-owner": [
    APP,
    [["Tasks", "first", 800], ["Continue hiring for the customer success lead role", "first", 400]],
    "/tasks",
    true,
    [{ text: "Continue hiring for the customer success lead role", wait: 400 }],
  ],
  "app-detail-share": [null, [], "/meetings/1", true],
  "invite-new": [null, [], "/accept-invite?token=new", false],
  "invite-signin": [null, [], "/accept-invite?token=existing", false],
  "invite-accept": [null, [], "/accept-invite?token=existing", true],
  "invite-dead": [null, [], "/accept-invite?token=dead", false],
  "signup-team-invite": [null, [], "/signup?invite=new", false],
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
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: values.tz });
  if (signedIn) await context.addCookies([{ name: "session", value: "owner", url: NEXT }]);
  // The prototype has its own fixed "today" (2 Nov 2026); `--now` gives the Next page the same one.
  if (side === "next" && values.now) await context.clock.setFixedTime(new Date(values.now));
  const page = await context.newPage();
  if (side === "next") {
    page.on("console", (message) => message.type() === "error" && console.log(`  console error on ${url}: ${message.text()}`));
    page.on("pageerror", (error) => console.log(`  page error on ${url}: ${error.message}`));
  }
  // The Meetings list polls while a meeting is being worked on, so the network may never go idle.
  await page.goto(url, { waitUntil: "load" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
  for (const step of clicks) {
    if (!Array.isArray(step)) {
      if (step.fill) {
        await page.getByPlaceholder(step.fill).first().fill(step.value);
      } else if (step.select) {
        const field =
          side === "design"
            ? page.locator("select", { hasText: step.design })
            : page.getByRole("combobox", { name: step.next });
        await field.first().selectOption({ label: step.select });
      } else if (step.click) {
        await page.getByRole("button", { name: step.click }).first().evaluate((el) => el.click());
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
